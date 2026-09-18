import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	PENDING_ENTRY_TYPE,
	PendingStore,
	isRecord,
	parseDurableEntry,
	parseIngress,
	parseReplySuccess,
	serializeConsumedEntry,
	serializePendingEntry,
	serializeRemindedEntry,
} from "../core/index.js";
import type { DurableEntry, ReplyPayload } from "../core/index.js";

interface SessionState {
	readonly pending: PendingStore;
	sessionRef: string | undefined;
}

function toReplyPayload(params: unknown): ReplyPayload {
	if (!isRecord(params) || typeof params.kind !== "string") {
		throw new Error(JSON.stringify({ error: "invalid_reply_payload" }));
	}

	switch (params.kind) {
		case "text": {
			if (typeof params.text !== "string") throw new Error(JSON.stringify({ error: "text_required" }));
			if (params.sticker !== undefined && typeof params.sticker !== "string") {
				throw new Error(JSON.stringify({ error: "sticker_must_be_string" }));
			}
			return params.sticker === undefined
				? { kind: "text", text: params.text }
				: { kind: "text", text: params.text, sticker: params.sticker };
		}
		case "text_template": {
			if (typeof params.template !== "string" || !isStringRecord(params.fields)) {
				throw new Error(JSON.stringify({ error: "template_and_string_fields_required" }));
			}
			if (params.sticker !== undefined && typeof params.sticker !== "string") {
				throw new Error(JSON.stringify({ error: "sticker_must_be_string" }));
			}
			return params.sticker === undefined
				? { kind: "text_template", template: params.template, fields: params.fields }
				: { kind: "text_template", template: params.template, fields: params.fields, sticker: params.sticker };
		}
		case "image_template": {
			if (typeof params.template !== "string" || !isRecord(params.fields)) {
				throw new Error(JSON.stringify({ error: "template_and_fields_required" }));
			}
			if (params.sticker !== undefined && typeof params.sticker !== "string") {
				throw new Error(JSON.stringify({ error: "sticker_must_be_string" }));
			}
			return params.sticker === undefined
				? { kind: "image_template", template: params.template, fields: params.fields }
				: { kind: "image_template", template: params.template, fields: params.fields, sticker: params.sticker };
		}
		case "sticker":
			if (typeof params.sticker !== "string") throw new Error(JSON.stringify({ error: "sticker_required" }));
			return { kind: "sticker", sticker: params.sticker };
		case "file": {
			if (typeof params.path !== "string") throw new Error(JSON.stringify({ error: "path_required" }));
			if (params.caption !== undefined && typeof params.caption !== "string") {
				throw new Error(JSON.stringify({ error: "caption_must_be_string" }));
			}
			return params.caption === undefined
				? { kind: "file", path: params.path }
				: { kind: "file", path: params.path, caption: params.caption };
		}
		default:
			throw new Error(JSON.stringify({ error: "unsupported_reply_kind", kind: params.kind }));
	}
}


function isStringRecord(value: unknown): value is Record<string, string> {
	if (!isRecord(value)) return false;
	for (const item of Object.values(value)) {
		if (typeof item !== "string") return false;
	}
	return true;
}
function sessionKey(ctx: ExtensionContext): string {
	return ctx.sessionManager.getSessionId();
}


function execFailureText(stdout: string, stderr: string): string {
	if (stderr.length > 0) return stderr;
	if (stdout.length > 0) return stdout;
	return JSON.stringify({ error: "reply_failed" });
}

function errorText(error: unknown): string {
	if (error instanceof Error) return error.message;
	return JSON.stringify(error) ?? JSON.stringify({ error: "unknown_failure" });
}

function stateFor(states: Map<string, SessionState>, key: string): SessionState {
	const existing = states.get(key);
	if (existing !== undefined) return existing;
	const created: SessionState = { pending: new PendingStore(), sessionRef: undefined };
	states.set(key, created);
	return created;
}

function restoreState(ctx: ExtensionContext): SessionState {
	const durableEntries: DurableEntry[] = [];
	for (const entry of ctx.sessionManager.getBranch()) {
		if (entry.type !== "custom") continue;
		const parsed = parseDurableEntry(entry.customType, entry.data);
		if (parsed !== undefined) durableEntries.push(parsed);
	}

	let sessionRef: string | undefined;
	for (const entry of durableEntries) {
		if (entry.customType === PENDING_ENTRY_TYPE) sessionRef = entry.data.session;
	}
	return { pending: PendingStore.fromEntries(durableEntries), sessionRef };
}

export default function alicedev(pi: ExtensionAPI): void {
	const states = new Map<string, SessionState>();
	const z = pi.zod;

	pi.on("session_start", (_event, ctx) => {
		states.set(sessionKey(ctx), restoreState(ctx));
	});

	pi.on("session_shutdown", (_event, ctx) => {
		states.delete(sessionKey(ctx));
	});

	pi.registerCommand("chat_ingress", {
		description: "Bridge-only structured chat ingress",
		handler: async (raw, ctx) => {
			const ingress = parseIngress(raw);
			const state = stateFor(states, sessionKey(ctx));
			state.sessionRef = ingress.session;
			state.pending.add(ingress.msg, ingress.text);
			const entry = serializePendingEntry(ingress.session, ingress.msg);
			pi.appendEntry(entry.customType, entry.data);
			pi.sendUserMessage(ingress.text);
		},
	});

	pi.registerTool({
		name: "chat_reply",
		label: "Chat Reply",
		description: "Send the current paseo answer to the originating chat session",
		parameters: z.union([
			z.object({
				message_id: z.string().optional(),
				kind: z.literal("text"),
				text: z.string(),
				sticker: z.string().optional(),
			}),
			z.object({
				message_id: z.string().optional(),
				kind: z.literal("text_template"),
				template: z.string(),
				fields: z.record(z.string(), z.string()),
				sticker: z.string().optional(),
			}),
			z.object({
				message_id: z.string().optional(),
				kind: z.literal("image_template"),
				template: z.string(),
				fields: z.record(z.string(), z.unknown()),
				sticker: z.string().optional(),
			}),
			z.object({
				message_id: z.string().optional(),
				kind: z.literal("sticker"),
				sticker: z.string(),
			}),
			z.object({
				message_id: z.string().optional(),
				kind: z.literal("file"),
				path: z.string(),
				caption: z.string().optional(),
			}),
		]),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const state = states.get(sessionKey(ctx));
			if (state === undefined) {
				return { content: [{ type: "text", text: JSON.stringify({ error: "session_unknown" }) }], isError: true };
			}
			const msgs = state.pending.currentMsgs();
			if (msgs.length === 0 || state.sessionRef === undefined) {
				return { content: [{ type: "text", text: JSON.stringify({ error: "no_pending_messages" }) }], isError: true };
			}

			let payload: ReplyPayload;
			try {
				payload = toReplyPayload(params);
			} catch (error: unknown) {
				return { content: [{ type: "text", text: errorText(error) }], isError: true };
			}

			const replyId = state.pending.replyIdFor(msgs);
			const args = [
				"--session",
				state.sessionRef,
				"--reply-id",
				replyId,
				"--msgs",
				msgs.join(","),
				"--json",
				JSON.stringify(payload),
			];
			try {
				const result = await pi.exec("alicedev-reply", args);
				const status = result.code === 0 ? parseReplySuccess(result.stdout.trim() ? JSON.parse(result.stdout) : undefined) : undefined;
				if (result.code === 0 && status !== undefined) {
					state.pending.markConsumed(msgs);
					const entry = serializeConsumedEntry(msgs, replyId);
					pi.appendEntry(entry.customType, entry.data);
					return { content: [{ type: "text", text: "已回复" }] };
				}
				return { content: [{ type: "text", text: execFailureText(result.stdout, result.stderr) }], isError: true };
			} catch (error: unknown) {
				return { content: [{ type: "text", text: errorText(error) }], isError: true };
			}
		},
	});

	pi.on("session_stop", async (event, ctx) => {
		if (event.signal?.aborted) return;
		const state = states.get(sessionKey(ctx));
		if (state === undefined) return;
		const msgs = state.pending.currentMsgs();
		if (msgs.length === 0 || state.pending.wasReminded(msgs)) return;
		state.pending.markReminded(msgs);
		const entry = serializeRemindedEntry(msgs);
		pi.appendEntry(entry.customType, entry.data);
		return {
			continue: true,
			additionalContext:
				"<alicedev-unreplied>" +
				JSON.stringify({ msgs, texts: state.pending.textsFor(msgs) }) +
				"</alicedev-unreplied> 你还没有通过 chat_reply 回复上面的消息，请先调用 chat_reply 再结束本轮。",
		};
	});
}
