#!/usr/bin/env node

const MAX_RETRIES = 4;
const MAX_ELAPSED_MS = 30_000;
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000];

function writeJson(stream, value) {
	stream.write(`${JSON.stringify(value)}\n`);
}

class UsageError extends Error {}

function parseArgs(argv) {
	const values = new Map();
	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		if (!arg.startsWith("--")) throw new UsageError(`unexpected argument: ${arg}`);
		const key = arg.slice(2);
		if (key !== "agent" && key !== "reply-id" && key !== "msgs" && key !== "json") {
			throw new UsageError(`unknown argument: ${arg}`);
		}
		const value = argv[index + 1];
		if (value === undefined || value.startsWith("--")) throw new UsageError(`missing value for ${arg}`);
		values.set(key, value);
		index += 1;
	}
	for (const key of ["agent", "reply-id", "msgs", "json"]) {
		if (!values.has(key)) throw new UsageError(`missing --${key}`);
	}
	let payload;
	try {
		payload = JSON.parse(values.get("json"));
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
	const msgsValue = values.get("msgs");
	const request = {
		agent: values.get("agent"),
		reply_id: values.get("reply-id"),
		msgs: msgsValue === "" ? [] : msgsValue.split(","),
	};
	if (payload.reply !== undefined) request.reply = payload.reply;
	if (payload.transition !== undefined) request.transition = payload.transition;
	return request;
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

function sleep(milliseconds) {
	return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function fetchWithinDeadline(url, options, deadline) {
	const remaining = deadline - Date.now();
	if (remaining <= 0) throw new Error("retry deadline exceeded");
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), remaining);
	try {
		return await fetch(url, { ...options, signal: controller.signal });
	} finally {
		clearTimeout(timer);
	}
}

async function probeHealth(baseUrl, headers, deadline) {
	try {
		await fetchWithinDeadline(`${baseUrl}/v1/health`, { headers }, Math.min(deadline, Date.now() + 1_000));
	} catch {
		// The health probe is advisory; the reply request controls the retry decision.
	}
}

async function retryAfterTransient(baseUrl, headers, deadline, retry) {
	if (retry >= MAX_RETRIES) return false;
	await probeHealth(baseUrl, headers, deadline);
	const remaining = deadline - Date.now();
	if (remaining <= 0) return false;
	const delay = BACKOFF_MS[retry] ?? 0;
	if (delay >= remaining) {
		await sleep(remaining);
		return false;
	}
	await sleep(delay);
	return true;
}

async function requestReply(request) {
	const baseUrl = (process.env.ALICEDEV_INTERNAL_API || "http://astrbot:6200").replace(/\/+$/, "");
	const token = process.env.ALICEDEV_INTERNAL_TOKEN || "";
	const headers = {
		"content-type": "application/json",
		"x-alicedev-token": token,
	};
	const body = JSON.stringify(request);
	const deadline = Date.now() + MAX_ELAPSED_MS;
	let retry = 0;

	while (true) {
		let response;
		try {
			response = await fetchWithinDeadline(
				`${baseUrl}/v1/reply`,
				{ method: "POST", headers, body },
				deadline,
			);
		} catch {
			if (!(await retryAfterTransient(baseUrl, headers, deadline, retry))) {
				writeJson(process.stderr, { error: "unreachable" });
				return 1;
			}
			retry += 1;
			continue;
		}

		const text = await response.text();
		const value = responseValue(text);
		if (response.status === 503) {
			if (!(await retryAfterTransient(baseUrl, headers, deadline, retry))) {
				writeJson(process.stderr, { error: "unreachable" });
				return 1;
			}
			retry += 1;
			continue;
		}

		if (response.status === 202) {
			if (!isObject(value) || (value.status !== "queued" && value.status !== "replayed")) {
				writeJson(process.stderr, isObject(value) ? value : { error: "invalid_response" });
				return 1;
			}
			writeJson(process.stdout, value);
			return 0;
		}

		writeJson(process.stderr, isObject(value) ? value : { error: `http_${response.status}` });
		return 1;
	}
}

// Exit codes: 0 = enqueued (stdout {"status":"queued"|"replayed"}),
// 1 = rejected or unreachable (stderr JSON), 2 = invalid arguments (stderr JSON).
try {
	const request = parseArgs(process.argv.slice(2));
	process.exitCode = await requestReply(request);
} catch (error) {
	writeJson(process.stderr, { error: error instanceof Error ? error.message : "invalid_arguments" });
	process.exitCode = error instanceof UsageError ? 2 : 1;
}
