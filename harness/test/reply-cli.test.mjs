import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const cli = join(dirname(fileURLToPath(import.meta.url)), "../reply-cli/index.mjs");

/** Serve scripted [status, body] responses for POST /v1/reply; record request bodies. */
async function withServer(script, fn) {
	const bodies = [];
	let call = 0;
	const server = createServer((req, res) => {
		let body = "";
		req.on("data", chunk => (body += chunk));
		req.on("end", () => {
			if (req.url === "/v1/health") return res.writeHead(200).end("{}");
			bodies.push({ token: req.headers["x-alicedev-token"], body: JSON.parse(body) });
			const [status, payload] = script[Math.min(call, script.length - 1)];
			call += 1;
			res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(payload));
		});
	});
	await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
	try {
		return await fn(`http://127.0.0.1:${server.address().port}`, bodies);
	} finally {
		server.close();
	}
}

function run(base, args) {
	return new Promise(resolve => {
		const child = spawn(process.execPath, [cli, ...args], {
			env: { ...process.env, ALICEDEV_INTERNAL_API: base, ALICEDEV_INTERNAL_TOKEN: "tok" },
		});
		let stdout = "";
		let stderr = "";
		child.stdout.on("data", d => (stdout += d));
		child.stderr.on("data", d => (stderr += d));
		child.on("close", code => resolve({ code, stdout, stderr }));
	});
}

const baseArgs = json => ["--agent", "a_abcdefghij", "--reply-id", "r-1", "--msgs", "m_1,m_2", "--json", JSON.stringify(json)];

test("202 queued -> exit 0, stdout JSON, request shape", async () => {
	await withServer([[202, { status: "queued" }]], async (base, bodies) => {
		const res = await run(base, baseArgs({ reply: { kind: "text", text: "hi" }, transition: { state: "done" } }));
		assert.equal(res.code, 0, res.stderr);
		assert.deepEqual(JSON.parse(res.stdout), { status: "queued" });
		assert.equal(bodies[0].token, "tok");
		assert.deepEqual(bodies[0].body, {
			agent: "a_abcdefghij",
			reply_id: "r-1",
			msgs: ["m_1", "m_2"],
			reply: { kind: "text", text: "hi" },
			transition: { state: "done" },
		});
	});
});

test("503 then 202 replayed -> retried, exit 0", async () => {
	await withServer([[503, { error: "draining" }], [202, { status: "replayed" }]], async (base, bodies) => {
		const res = await run(base, baseArgs({ transition: { state: "done" } }));
		assert.equal(res.code, 0, res.stderr);
		assert.deepEqual(JSON.parse(res.stdout), { status: "replayed" });
		assert.equal(bodies.length, 2);
	});
});

test("409 -> exit 1, error on stderr, no retry", async () => {
	await withServer([[409, { error: "agent_not_current" }]], async (base, bodies) => {
		const res = await run(base, baseArgs({ reply: { kind: "text", text: "x" } }));
		assert.equal(res.code, 1);
		assert.deepEqual(JSON.parse(res.stderr), { error: "agent_not_current" });
		assert.equal(res.stdout, "");
		assert.equal(bodies.length, 1);
	});
});

test("200 (old contract) is not success", async () => {
	await withServer([[200, { status: "sent" }]], async base => {
		const res = await run(base, baseArgs({ reply: { kind: "text", text: "x" } }));
		assert.equal(res.code, 1);
	});
});

test("invalid arguments -> exit 2", async () => {
	const res = await run("http://127.0.0.1:9", ["--session", "s_x"]);
	assert.equal(res.code, 2);
	const empty = await run("http://127.0.0.1:9", baseArgs({}));
	assert.equal(empty.code, 2);
	assert.match(empty.stderr, /reply and\/or transition/);
});
