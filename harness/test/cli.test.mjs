import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const cli = join(dirname(fileURLToPath(import.meta.url)), "../cli/index.mjs");

/** Serve scripted JSON responses and record non-health API requests. */
async function withServer(script, fn) {
	const requests = [];
	let healthProbes = 0;
	const server = createServer((req, res) => {
		let body = "";
		req.on("data", chunk => (body += chunk));
		req.on("end", () => {
			if (req.url === "/v1/health") {
				healthProbes += 1;
				return res.writeHead(200, { "content-type": "application/json" }).end("{}");
			}
			const request = {
				method: req.method,
				url: req.url,
				token: req.headers["x-alicedev-token"],
				body: body === "" ? undefined : JSON.parse(body),
			};
			requests.push(request);
			const [status, payload] = script[Math.min(requests.length - 1, script.length - 1)];
			if (status === "reset") {
				res.destroy();
				return;
			}
			res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(payload));
		});
	});
	await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
	try {
		return await fn(`http://127.0.0.1:${server.address().port}`, requests, () => healthProbes);
	} finally {
		server.closeAllConnections();
		await new Promise(resolve => server.close(resolve));
	}
}

function runCli(base, args) {
	return new Promise((resolve, reject) => {
		const child = spawn(process.execPath, [cli, ...args], {
			env: { ...process.env, ALICEDEV_INTERNAL_API: base, ALICEDEV_INTERNAL_TOKEN: "tok" },
		});
		let stdout = "";
		let stderr = "";
		child.stdout.on("data", data => (stdout += data));
		child.stderr.on("data", data => (stderr += data));
		child.on("error", reject);
		child.on("close", code => resolve({ code, stdout, stderr }));
	});
}

const replyArgs = payload => [
	"reply",
	"--agent",
	"a_abcdefghij",
	"--reply-id",
	"r-1",
	"--msgs",
	"m_1,m_2",
	"--json",
	JSON.stringify(payload),
];

function commandError(result, expectedCode) {
	assert.equal(result.code, expectedCode, result.stderr);
	assert.equal(result.stdout, "");
	return JSON.parse(result.stderr);
}

test("reply 202 queued keeps the existing request and output semantics", async () => {
	await withServer([[202, { status: "queued" }]], async (base, requests) => {
		const result = await runCli(base, replyArgs({ reply: { kind: "text", text: "hi" }, transition: { state: "done" } }));
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), { status: "queued" });
		assert.equal(requests[0].method, "POST");
		assert.equal(requests[0].url, "/v1/reply");
		assert.equal(requests[0].token, "tok");
		assert.deepEqual(requests[0].body, {
			agent: "a_abcdefghij",
			reply_id: "r-1",
			msgs: ["m_1", "m_2"],
			reply: { kind: "text", text: "hi" },
			transition: { state: "done" },
		});
	});
});

test("reply retries 503 and accepts replayed", async () => {
	await withServer([[503, { error: "draining" }], [202, { status: "replayed" }]], async (base, requests, healthProbes) => {
		const result = await runCli(base, replyArgs({ transition: { state: "done" } }));
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), { status: "replayed" });
		assert.equal(requests.length, 2);
		assert.ok(healthProbes() >= 1);
	});
});

test("reply forwards a bot conflict without retrying", async () => {
	await withServer([[409, { error: "agent_not_current" }]], async (base, requests) => {
		const result = await runCli(base, replyArgs({ reply: { kind: "text", text: "x" } }));
		assert.deepEqual(commandError(result, 1), { error: "agent_not_current" });
		assert.equal(requests.length, 1);
	});
});

test("reply does not treat an old 200 response as success", async () => {
	await withServer([[200, { status: "sent" }]], async (base, requests) => {
		const result = await runCli(base, replyArgs({ reply: { kind: "text", text: "x" } }));
		assert.deepEqual(commandError(result, 1), { status: "sent" });
		assert.equal(requests.length, 1);
	});
});

test("commands GET returns bot JSON and sends agent in the query", async () => {
	await withServer([[200, { agent: "a_abcdefghij", commands: [] }]], async (base, requests) => {
		const result = await runCli(base, ["commands", "--agent", "a_abcdefghij"]);
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), { agent: "a_abcdefghij", commands: [] });
		assert.deepEqual(requests, [{
			method: "GET",
			url: "/v1/commands?agent=a_abcdefghij",
			token: "tok",
			body: undefined,
		}]);
	});
});

test("run reuses one generated call_id across a 503 retry and forwards chat and quote", async () => {
	await withServer([[503, { error: "draining" }], [200, { result: "created", data: { no: 4 } }]], async (base, requests, healthProbes) => {
		const result = await runCli(base, [
			"run",
			"--agent",
			"a_abcdefghij",
			"--chat",
			"group:42",
			"--quote",
			"m_123",
			"/需求 支持中文界面",
		]);
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), { result: "created", data: { no: 4 } });
		assert.equal(requests.length, 2);
		assert.ok(healthProbes() >= 1);
		const { call_id: callId, ...firstBody } = requests[0].body;
		assert.deepEqual(firstBody, {
			agent: "a_abcdefghij",
			text: "/需求 支持中文界面",
			chat: "group:42",
			quote: "m_123",
		});
		assert.match(callId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
		assert.equal(requests[0].method, "POST");
		assert.equal(requests[0].url, "/v1/commands");
		assert.equal(requests[0].token, "tok");
		assert.equal(requests[1].body.call_id, callId);
		assert.deepEqual(requests[1].body, requests[0].body);
	});
});

test("run retries a reset connection and reuses its call_id", async () => {
	await withServer([["reset", null], [200, { result: "ok" }]], async (base, requests, healthProbes) => {
		const result = await runCli(base, ["run", "--agent", "a_abcdefghij", "/需求 重试"]);
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), { result: "ok" });
		assert.equal(requests.length, 2);
		assert.ok(healthProbes() >= 1);
		assert.equal(requests[1].body.call_id, requests[0].body.call_id);
	});
});

test("run honors an explicitly supplied call_id", async () => {
	await withServer([[200, { result: "ok" }]], async (base, requests) => {
		const result = await runCli(base, [
			"run",
			"--agent",
			"a_abcdefghij",
			"--call-id",
			"call-fixed",
			"/需求 支持中文界面",
		]);
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(requests[0].body, {
			agent: "a_abcdefghij",
			call_id: "call-fixed",
			text: "/需求 支持中文界面",
		});
	});
});

test("commands and run forward bot 4xx error JSON without retrying", async () => {
	await withServer([[403, { error: "command_not_allowed" }]], async (base, requests) => {
		const result = await runCli(base, ["run", "--agent", "a_abcdefghij", "/管家 不可调用"]);
		assert.deepEqual(commandError(result, 1), { error: "command_not_allowed" });
		assert.equal(requests.length, 1);
	});
	await withServer([[409, { error: "agent_not_current" }]], async (base, requests) => {
		const result = await runCli(base, ["commands", "--agent", "a_abcdefghij"]);
		assert.deepEqual(commandError(result, 1), { error: "agent_not_current" });
		assert.equal(requests.length, 1);
	});
});

test("usage errors exit 2 without making an API request; help prints usage", async () => {
	const missingAgent = await runCli("http://127.0.0.1:9", ["commands"]);
	assert.deepEqual(commandError(missingAgent, 2), { error: "missing --agent" });
	const missingText = await runCli("http://127.0.0.1:9", ["run", "--agent", "a_abcdefghij"]);
	assert.equal(missingText.code, 2, missingText.stderr);
	assert.match(missingText.stderr, /command line/);
	const unknownReplyFlag = await runCli("http://127.0.0.1:9", ["reply", "--session", "s_x"]);
	assert.deepEqual(commandError(unknownReplyFlag, 2), { error: "unknown argument: --session" });
	const invalidReply = await runCli("http://127.0.0.1:9", replyArgs({}));
	assert.equal(invalidReply.code, 2, invalidReply.stderr);
	assert.match(invalidReply.stderr, /reply and\/or transition/);
	const help = await runCli("http://127.0.0.1:9", ["--help"]);
	assert.equal(help.code, 0, help.stderr);
	assert.match(help.stdout, /alicedev commands --agent/);
});
