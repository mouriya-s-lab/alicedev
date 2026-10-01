#!/usr/bin/env node

import { randomUUID } from "node:crypto";

const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000];
const USAGE = `Usage:
  alicedev reply --agent <ref> --reply-id <uuid> --msgs <m1,m2> --json '<payload>'
  alicedev tools --agent <ref>
  alicedev tool <name> --agent <ref> [--call-id <id>] --json '<args>'
`;

class UsageError extends Error {}

function writeJson(stream, value) {
	stream.write(`${JSON.stringify(value)}\n`);
}

function responseValue(text) {
	try {
		return JSON.parse(text);
	} catch {
		return undefined;
	}
}

function isObject(value) {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseFlags(argv, allowed, rejectDuplicates = false) {
	const values = new Map();
	const positional = [];
	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		if (!arg.startsWith("--")) {
			positional.push(arg);
			continue;
		}
		const key = arg.slice(2);
		if (!allowed.has(key)) throw new UsageError(`unknown argument: ${arg}`);
		if (rejectDuplicates && values.has(key)) throw new UsageError(`duplicate argument: ${arg}`);
		const value = argv[index + 1];
		if (value === undefined || value.startsWith("--")) throw new UsageError(`missing value for ${arg}`);
		values.set(key, value);
		index += 1;
	}
	return { values, positional };
}

function required(values, key) {
	if (!values.has(key)) throw new UsageError(`missing --${key}`);
	return values.get(key);
}

function parseReply(argv) {
	const { values, positional } = parseFlags(argv, new Set(["agent", "reply-id", "msgs", "json"]));
	if (positional.length > 0) throw new UsageError(`unexpected argument: ${positional[0]}`);
	const agent = required(values, "agent");
	const replyId = required(values, "reply-id");
	const msgsValue = required(values, "msgs");
	const raw = required(values, "json");
	let payload;
	try {
		payload = JSON.parse(raw);
	} catch {
		throw new UsageError("--json must contain valid JSON");
	}
	if (!isObject(payload)) throw new UsageError("--json must be an object {reply?, transition?}");
	if (payload.reply === undefined && payload.transition === undefined) {
		throw new UsageError("--json needs reply and/or transition");
	}
	for (const key of Object.keys(payload)) {
		if (key !== "reply" && key !== "transition") throw new UsageError(`--json has unknown key: ${key}`);
	}
	const request = {
		agent,
		reply_id: replyId,
		msgs: msgsValue === "" ? [] : msgsValue.split(","),
	};
	if (payload.reply !== undefined) request.reply = payload.reply;
	if (payload.transition !== undefined) request.transition = payload.transition;
	return request;
}

function parseTools(argv) {
	const { values, positional } = parseFlags(argv, new Set(["agent"]), true);
	if (positional.length > 0) throw new UsageError(`unexpected argument: ${positional[0]}`);
	const agent = required(values, "agent");
	if (agent.trim().length === 0) throw new UsageError("--agent must not be empty");
	return agent;
}

function parseTool(argv) {
	const { values, positional } = parseFlags(argv, new Set(["agent", "call-id", "json"]), true);
	const agent = required(values, "agent");
	if (agent.trim().length === 0) throw new UsageError("--agent must not be empty");
	if (positional.length !== 1 || !/^[a-z][a-z_]*$/.test(positional[0])) {
		throw new UsageError("tool requires one name containing lowercase letters and underscores");
	}
	const raw = required(values, "json");
	let args;
	try {
		args = JSON.parse(raw);
	} catch {
		throw new UsageError("--json must contain valid JSON");
	}
	if (!isObject(args)) throw new UsageError("--json must be an object");
	const callId = values.get("call-id") ?? randomUUID();
	if (callId.trim().length === 0) throw new UsageError("--call-id must not be empty");
	return { name: positional[0], request: { agent, call_id: callId, args } };
}

function sleep(milliseconds) {
	return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function fetchWithinDeadline(url, options, deadline) {
	const remaining = deadline - Date.now();
	if (remaining <= 0) throw new Error("request budget exhausted");
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), remaining);
	try {
		return await fetch(url, { ...options, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
}

async function fetchJsonWithinDeadline(url, options, deadline) {
	const remaining = deadline - Date.now();
	if (remaining <= 0) throw new Error("request budget exhausted");
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), remaining);
	try {
		const response = await fetch(url, { ...options, signal: controller.signal });
		return { response, value: responseValue(await response.text()) };
	} finally {
		clearTimeout(timer);
	}
}

function isConnectionFailure(error) {
	const seen = new Set();
	let current = error;
	while (current !== null && typeof current === "object" && !seen.has(current)) {
		seen.add(current);
		if (current.code === "ECONNREFUSED" || current.code === "ECONNRESET") return true;
		// Node fetch represents a peer reset as this Undici socket error.
		if (current.code === "UND_ERR_SOCKET" && current.message === "other side closed") return true;
		current = current.cause;
	}
	return false;
}

async function probeHealth(baseUrl, headers, deadline) {
	try {
		await fetchWithinDeadline(`${baseUrl}/v1/health`, { headers }, Math.min(deadline, Date.now() + 1_000));
	} catch {
		// The health probe is advisory; the API request controls the retry decision.
	}
}

async function retryAfterTransient(baseUrl, headers, deadline, retry) {
	if (retry >= BACKOFF_MS.length) return false;
	await probeHealth(baseUrl, headers, deadline);
	const remaining = deadline - Date.now();
	if (remaining <= 0) return false;
	const delay = BACKOFF_MS[retry];
	if (delay >= remaining) {
		await sleep(remaining);
		return false;
	}
	await sleep(delay);
	return true;
}

async function requestJson(path, { method = "GET", body, budgetMs, successStatus, isSuccess }) {
	const baseUrl = (process.env.ALICEDEV_INTERNAL_API || "http://astrbot:6200").replace(/\/+$/, "");
	const token = process.env.ALICEDEV_INTERNAL_TOKEN || "";
	const headers = {
		"content-type": "application/json",
		"x-alicedev-token": token,
	};
	const options = { method, headers };
	if (body !== undefined) options.body = JSON.stringify(body);
	const deadline = Date.now() + budgetMs;
	let retry = 0;

	while (true) {
		let response;
		let value;
		try {
			const result = await fetchJsonWithinDeadline(`${baseUrl}${path}`, options, deadline);
			response = result.response;
			value = result.value;
		} catch (error) {
			if (!isConnectionFailure(error) || !(await retryAfterTransient(baseUrl, headers, deadline, retry))) {
				return { ok: false, error: { error: "unreachable" } };
			}
			retry += 1;
			continue;
		}

		if (response.status === 503) {
			if (!(await retryAfterTransient(baseUrl, headers, deadline, retry))) {
				return { ok: false, error: { error: "unreachable" } };
			}
			retry += 1;
			continue;
		}

		if (response.status === successStatus && isSuccess(value)) {
			return { ok: true, value };
		}
		return {
			ok: false,
			error: isObject(value) ? value : { error: response.status === successStatus ? "invalid_response" : `http_${response.status}` },
		};
	}
}

async function invokeTool(argv) {
	const { name, request } = parseTool(argv);
	const result = await requestJson(`/v1/tools/${encodeURIComponent(name)}`, {
		method: "POST",
		body: request,
		budgetMs: 120_000,
		successStatus: 200,
		isSuccess: isObject,
	});
	if (!result.ok) {
		writeJson(process.stderr, result.error);
		return 1;
	}
	writeJson(process.stdout, result.value);
	return 0;
}

async function listTools(argv) {
	const agent = parseTools(argv);
	const query = new URLSearchParams({ agent });
	const result = await requestJson(`/v1/tools?${query}`, {
		budgetMs: 30_000,
		successStatus: 200,
		isSuccess: isObject,
	});
	if (!result.ok) {
		writeJson(process.stderr, result.error);
		return 1;
	}
	writeJson(process.stdout, result.value);
	return 0;
}

async function reply(argv) {
	const request = parseReply(argv);
	const result = await requestJson("/v1/reply", {
		method: "POST",
		body: request,
		budgetMs: 30_000,
		successStatus: 202,
		isSuccess: value => isObject(value) && (value.status === "queued" || value.status === "replayed"),
	});
	if (!result.ok) {
		writeJson(process.stderr, result.error);
		return 1;
	}
	writeJson(process.stdout, result.value);
	return 0;
}

async function main(argv) {
	if (
		(argv.length === 1 && argv[0] === "--help") ||
		(argv.length === 2 && ["reply", "tools", "tool"].includes(argv[0]) && argv[1] === "--help")
	) {
		process.stdout.write(USAGE);
		return 0;
	}
	const [command, ...args] = argv;
	if (command === undefined) throw new UsageError("missing command");
	switch (command) {
		case "reply":
			return reply(args);
		case "tools":
			return listTools(args);
		case "tool":
			return invokeTool(args);
		default:
			throw new UsageError(`unknown command: ${command}`);
	}
}

try {
	process.exitCode = await main(process.argv.slice(2));
} catch (error) {
	writeJson(process.stderr, { error: error instanceof Error ? error.message : "invalid_arguments" });
	process.exitCode = error instanceof UsageError ? 2 : 1;
}
