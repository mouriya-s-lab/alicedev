import assert from "node:assert/strict";
import { test } from "node:test";
import {
	PendingStore,
	parseDurableEntry,
	parseIngress,
	parseReplyRequest,
	parseReplySuccess,
	serializePendingEntry,
} from "../dist/test/core.mjs";

test("ingress carries agent/msg/text", () => {
	const msg = parseIngress('{"agent":"a_abcdefghij","msg":"m_1","text":"hi\\nthere"}');
	assert.deepEqual(msg, { agent: "a_abcdefghij", msg: "m_1", text: "hi\nthere" });
	assert.throws(() => parseIngress('{"session":"s_x","msg":"m","text":"t"}'), /agent/);
	assert.throws(() => parseIngress("not json"), /JSON/);
});

test("pending entry persists text and round-trips", () => {
	const entry = serializePendingEntry("a_1", "m_1", "hello");
	assert.deepEqual(entry.data, { agent: "a_1", msg: "m_1", text: "hello" });
	const parsed = parseDurableEntry(entry.customType, entry.data);
	assert.deepEqual(parsed, entry);
	const store = PendingStore.fromEntries([parsed]);
	assert.deepEqual(store.textsFor(["m_1"]), ["hello"]);
});

test("legacy pending entries keyed by session still restore", () => {
	const parsed = parseDurableEntry("alicedev.pending", { session: "s_old", msg: "m_9" });
	assert.deepEqual(parsed?.data, { agent: "s_old", msg: "m_9", text: "" });
});

test("reply request needs reply and/or transition", () => {
	assert.deepEqual(parseReplyRequest({ reply: { kind: "text", text: "x" } }), { reply: { kind: "text", text: "x" } });
	assert.deepEqual(parseReplyRequest({ transition: { state: "done", data: { commit: "abc" } } }), {
		transition: { state: "done", data: { commit: "abc" } },
	});
	assert.deepEqual(
		parseReplyRequest({ reply: { kind: "image", paths: ["/r/a.png"], caption: "c" }, transition: { state: "s" } }),
		{ reply: { kind: "image", paths: ["/r/a.png"], caption: "c" }, transition: { state: "s" } },
	);
	assert.throws(() => parseReplyRequest({}), /reply_or_transition_required/);
	assert.throws(() => parseReplyRequest({ reply: { kind: "image", paths: [] } }), /paths_required/);
	assert.throws(() => parseReplyRequest({ transition: { state: "s", data: { n: 1 } } }), /string_map/);
	assert.throws(() => parseReplyRequest({ reply: { kind: "video" } }), /unsupported_reply_kind/);
});

test("reply success accepts queued|replayed only", () => {
	assert.deepEqual(parseReplySuccess({ status: "queued" }), { status: "queued" });
	assert.deepEqual(parseReplySuccess({ status: "replayed" }), { status: "replayed" });
	assert.equal(parseReplySuccess({ status: "sent" }), undefined);
});
