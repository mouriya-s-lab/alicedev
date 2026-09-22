import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	PENDING_ENTRY_TYPE,
	PendingStore,
	parseDurableEntry,
	parseIngress,
	parseReplyRequest,
	parseReplySuccess,
	serializeConsumedEntry,
	serializePendingEntry,
	serializeRemindedEntry,
} from "../core/index.js";
import type { DurableEntry, ReplyRequest } from "../core/index.js";

interface AgentState {
	readonly pending: PendingStore;
	agentRef: string | undefined;
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

function toolError(text: string) {
	return { content: [{ type: "text" as const, text }], isError: true };
}

function stateFor(states: Map<string, AgentState>, key: string): AgentState {
	const existing = states.get(key);
	if (existing !== undefined) return existing;
	const created: AgentState = { pending: new PendingStore(), agentRef: undefined };
	states.set(key, created);
	return created;
}

function restoreState(ctx: ExtensionContext): AgentState {
	const durableEntries: DurableEntry[] = [];
	for (const entry of ctx.sessionManager.getBranch()) {
		if (entry.type !== "custom") continue;
		const parsed = parseDurableEntry(entry.customType, entry.data);
		if (parsed !== undefined) durableEntries.push(parsed);
	}

	let agentRef: string | undefined;
	for (const entry of durableEntries) {
		if (entry.customType === PENDING_ENTRY_TYPE) agentRef = entry.data.agent;
	}
	return { pending: PendingStore.fromEntries(durableEntries), agentRef };
}

export default function alicedev(pi: ExtensionAPI): void {
	const states = new Map<string, AgentState>();
	const z = pi.zod;

	pi.on("session_start", (_event, ctx) => {
		states.set(sessionKey(ctx), restoreState(ctx));
	});

	// Once shut down the state is gone, so a late session_stop can never remind.
	pi.on("session_shutdown", (_event, ctx) => {
		states.delete(sessionKey(ctx));
	});

	pi.registerCommand("chat_ingress", {
		description: "Bridge-only structured chat ingress",
		handler: async (raw, ctx) => {
			const ingress = parseIngress(raw);
			const state = stateFor(states, sessionKey(ctx));
			state.agentRef = ingress.agent;
			state.pending.add(ingress.msg, ingress.text);
			const entry = serializePendingEntry(ingress.agent, ingress.msg, ingress.text);
			pi.appendEntry(entry.customType, entry.data);
			pi.sendUserMessage(ingress.text);
		},
	});

	const stringMap = z.record(z.string(), z.string());
	const replySchema = z.union([
		z.object({ kind: z.literal("text"), text: z.string(), sticker: z.string().optional() }),
		z.object({
			kind: z.literal("text_template"),
			template: z.string(),
			fields: stringMap,
			sticker: z.string().optional(),
		}),
		z.object({
			kind: z.literal("image_template"),
			template: z.string(),
			fields: z.record(z.string(), z.unknown()),
			sticker: z.string().optional(),
		}),
		z.object({ kind: z.literal("image"), paths: z.array(z.string()).min(1), caption: z.string().optional() }),
		z.object({ kind: z.literal("sticker"), sticker: z.string() }),
		z.object({ kind: z.literal("file"), path: z.string(), caption: z.string().optional() }),
	]);

	pi.registerTool({
		name: "chat_reply",
		label: "Chat Reply",
		description:
			"Reply to the chat and/or report a session state transition. Pass `reply` (what the group sees), " +
			"`transition` ({state, data}) to move the session to one of the allowed next states, or both. " +
			"At least one is required. Follow the 「回复方式」 section of your prompt for allowed kinds and states.",
		parameters: z.object({
			reply: replySchema.optional(),
			transition: z.object({ state: z.string(), data: stringMap.optional() }).optional(),
		}),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const state = states.get(sessionKey(ctx));
			if (state === undefined) return toolError(JSON.stringify({ error: "session_unknown" }));
			const msgs = state.pending.currentMsgs();
			if (msgs.length === 0 || state.agentRef === undefined) {
				return toolError(JSON.stringify({ error: "no_pending_messages" }));
			}

			let request: ReplyRequest;
			try {
				request = parseReplyRequest(params);
			} catch (error: unknown) {
				return toolError(errorText(error));
			}

			const replyId = state.pending.replyIdFor(msgs);
			const args = [
				"--agent",
				state.agentRef,
				"--reply-id",
				replyId,
				"--msgs",
				msgs.join(","),
				"--json",
				JSON.stringify(request),
			];
			try {
				const result = await pi.exec("alicedev-reply", args);
				const status =
					result.code === 0
						? parseReplySuccess(result.stdout.trim() ? JSON.parse(result.stdout) : undefined)
						: undefined;
				if (result.code === 0 && status !== undefined) {
					state.pending.markConsumed(msgs);
					const entry = serializeConsumedEntry(msgs, replyId);
					pi.appendEntry(entry.customType, entry.data);
					return { content: [{ type: "text", text: status.status === "replayed" ? "已回复（重放）" : "已回复" }] };
				}
				return toolError(execFailureText(result.stdout, result.stderr));
			} catch (error: unknown) {
				return toolError(errorText(error));
			}
		},
	});

	pi.on("session_stop", async (event, ctx) => {
		if (event.signal?.aborted) return;
		const state = states.get(sessionKey(ctx));
		if (state === undefined) return; // shut down or never ingested
		// Queued input will re-wake the loop; remind at the settle after it instead.
		if (ctx.hasPendingMessages()) return;
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
				"</alicedev-unreplied> 你还没有通过 chat_reply 回复上面的消息或报告状态，请先调用 chat_reply 再结束本轮。",
		};
	});
}
