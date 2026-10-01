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

const toolArgs = (args, extra = []) => [
	"tool",
	"requirement_add",
	"--agent",
	"a_abcdefghij",
	...extra,
	"--json",
	JSON.stringify(args),
];

test("tools retries 503 and returns the available typed capabilities", async () => {
	const capabilities = {
		agent: "a_abcdefghij",
		session_no: 4,
		chat_key: "group:42",
		tools: [{
			name: "requirement_add",
			summary: "Record a requirement",
			input_schema: {
				type: "object",
				properties: { text: { type: "string" } },
				required: ["text"],
				additionalProperties: false,
			},
		}],
	};
	await withServer([[503, { error: "draining" }], [200, capabilities]], async (base, requests) => {
		const result = await runCli(base, ["tools", "--agent", "a_abcdefghij"]);
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), capabilities);
		assert.equal(result.stderr, "");
		assert.deepEqual(requests.map(({ method, url, body }) => ({ method, url, body })), [
			{ method: "GET", url: "/v1/tools?agent=a_abcdefghij", body: undefined },
			{ method: "GET", url: "/v1/tools?agent=a_abcdefghij", body: undefined },
		]);
	});
});

test("tool retries 503 without changing its generated write identity or typed arguments", async () => {
	const args = { text: "支持中文界面", chat: "group:42", quote: "m_123" };
	const receipt = { result: "ok", requirement_id: 7 };
	await withServer([[503, { error: "draining" }], [200, receipt]], async (base, requests) => {
		const result = await runCli(base, toolArgs(args));
		assert.equal(result.code, 0, result.stderr);
		assert.deepEqual(JSON.parse(result.stdout), receipt);
		assert.equal(result.stderr, "");
		assert.equal(requests.length, 2);
		const callId = requests[0].body.call_id;
		assert.equal(typeof callId, "string");
		assert.notEqual(callId.trim(), "");
		for (const request of requests) {
			assert.equal(request.method, "POST");
			assert.equal(request.url, "/v1/tools/requirement_add");
			assert.deepEqual(request.body, { agent: "a_abcdefghij", call_id: callId, args });
		}
	});
});

test("tool preserves write identity after a reset with uncertain acceptance", async t => {
	const args = { text: "重试后不重复记录" };
	const receipt = { result: "ok", requirement_id: 8 };
	for (const suppliedId of [undefined, "call-fixed"]) {
		await t.test(suppliedId === undefined ? "generated identity" : "caller supplied identity", async () => {
			await withServer([["reset", null], [200, receipt]], async (base, requests) => {
				const extra = suppliedId === undefined ? [] : ["--call-id", suppliedId];
				const result = await runCli(base, toolArgs(args, extra));
				assert.equal(result.code, 0, result.stderr);
				assert.deepEqual(JSON.parse(result.stdout), receipt);
				assert.equal(result.stderr, "");
				assert.equal(requests.length, 2);
				const callId = requests[0].body.call_id;
				assert.equal(typeof callId, "string");
				assert.notEqual(callId.trim(), "");
				if (suppliedId !== undefined) assert.equal(callId, suppliedId);
				for (const request of requests) {
					assert.equal(request.method, "POST");
					assert.equal(request.url, "/v1/tools/requirement_add");
					assert.deepEqual(request.body, { agent: "a_abcdefghij", call_id: callId, args });
				}
			});
		});
	}
});

test("tools and tool return bot 4xx errors as failures without retrying", async t => {
	const cases = [
		{
			name: "tool permission denied",
			argv: toolArgs({ text: "不可调用" }),
			status: 403,
			error: { error: "tool_not_allowed" },
			method: "POST",
			url: "/v1/tools/requirement_add",
		},
		{
			name: "tool args rejected",
			argv: toolArgs({ text: "invalid field", recipient: "user:42" }),
			status: 400,
			error: { error: "invalid_payload" },
			method: "POST",
			url: "/v1/tools/requirement_add",
		},
		{
			name: "discovery agent no longer current",
			argv: ["tools", "--agent", "a_abcdefghij"],
			status: 409,
			error: { error: "agent_not_current" },
			method: "GET",
			url: "/v1/tools?agent=a_abcdefghij",
		},
		{
			name: "canonical unknown tool is rejected by the server",
			argv: ["tool", "unknown_tool", "--agent", "a_abcdefghij", "--json", "{}"],
			status: 404,
			error: { error: "unknown_tool" },
			method: "POST",
			url: "/v1/tools/unknown_tool",
		},
	];
	for (const item of cases) {
		await t.test(item.name, async () => {
			await withServer([[item.status, item.error]], async (base, requests) => {
				const result = await runCli(base, item.argv);
				assert.deepEqual(commandError(result, 1), item.error);
				assert.equal(requests.length, 1);
				assert.equal(requests[0].method, item.method);
				assert.equal(requests[0].url, item.url);
			});
		});
	}
});

test("invalid CLI input is rejected locally with exit 2 and a structured error", async t => {
	const validTool = toolArgs({ text: "valid" });
	const cases = [
		["missing discovery agent", ["tools"]],
		["empty discovery agent", ["tools", "--agent", ""]],
		["missing invocation agent", ["tool", "requirement_add", "--json", "{}"]],
		["empty invocation agent", ["tool", "requirement_add", "--agent", "", "--json", "{}"]],
		["missing tool name", ["tool", "--agent", "a_abcdefghij", "--json", "{}"]],
		["empty tool name", ["tool", "", "--agent", "a_abcdefghij", "--json", "{}"]],
		["missing tool JSON", ["tool", "requirement_add", "--agent", "a_abcdefghij"]],
		["missing agent flag value", ["tools", "--agent"]],
		["missing JSON flag value", ["tool", "requirement_add", "--agent", "a_abcdefghij", "--json"]],
		["missing call identity flag value", [...validTool, "--call-id"]],
		["flag where agent value is expected", ["tools", "--agent", "--json", "{}"]],
		["unknown discovery flag", ["tools", "--agent", "a_abcdefghij", "--unexpected", "x"]],
		["unknown invocation flag", [...validTool, "--unexpected", "x"]],
		["unexpected discovery positional", ["tools", "--agent", "a_abcdefghij", "extra"]],
		["extra tool name", [...validTool, "session_list"]],
		["removed commands verb", ["commands", "--agent", "a_abcdefghij"]],
		["removed run verb", ["run", "--agent", "a_abcdefghij", "/需求 不执行"]],
		["removed discovery chat flag", ["tools", "--agent", "a_abcdefghij", "--chat", "group:42"]],
		["removed discovery quote flag", ["tools", "--agent", "a_abcdefghij", "--quote", "m_1"]],
		["removed invocation chat flag", [...validTool, "--chat", "group:42"]],
		["removed invocation quote flag", [...validTool, "--quote", "m_1"]],
		["removed commands verb with help", ["commands", "--help"]],
		["removed run verb with help", ["run", "--help"]],
		["unknown verb with help", ["unknown", "--help"]],
		["help with extra standalone argument", ["--help", "extra"]],
		["help bypassing discovery validation", ["tools", "--agent", "a_abcdefghij", "--help"]],
		["help bypassing invocation validation", [...validTool, "--help"]],
		["duplicate discovery agent", ["tools", "--agent", "a_abcdefghij", "--agent", "a_other"]],
		["duplicate identical discovery agent", ["tools", "--agent", "a_abcdefghij", "--agent", "a_abcdefghij"]],
		["duplicate invocation agent", [...validTool, "--agent", "a_other"]],
		["duplicate invocation JSON", [...validTool, "--json", '{"text":"replacement"}']],
		["duplicate invocation call identity", [...validTool, "--call-id", "first", "--call-id", "second"]],
		["unknown reply flag", ["reply", "--session", "s_x"]],
		["empty reply payload", replyArgs({})],
	];
	for (const raw of ["", "{", '{"text":}']) {
		cases.push([`malformed tool JSON ${JSON.stringify(raw)}`, [...validTool.slice(0, -1), raw]]);
	}
	for (const raw of ["null", "[]", '"text"', "0", "true"]) {
		cases.push([`nonobject tool JSON ${raw}`, [...validTool.slice(0, -1), raw]]);
	}
	for (const name of [" ", "Session_get", "session-get", "session/get", "session_get?x", "/需求"]) {
		cases.push([`noncanonical tool name ${JSON.stringify(name)}`, [
			"tool", name, "--agent", "a_abcdefghij", "--json", "{}",
		]]);
	}
	for (const [name, argv] of cases) {
		await t.test(name, async () => {
			await withServer([[200, { result: "must_not_be_called" }]], async (base, requests, healthProbes) => {
				const error = commandError(await runCli(base, argv), 2);
				assert.equal(typeof error.error, "string");
				assert.notEqual(error.error.trim(), "");
				assert.deepEqual(requests, []);
				assert.equal(healthProbes(), 0);
			});
		});
	}
});
