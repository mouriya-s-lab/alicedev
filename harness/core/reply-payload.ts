import { isRecord } from "./guards.js";

/** Chat-visible reply (ARCHITECTURE §6 `ReplyPayload`). */
export type ReplyPayload =
	| {
			kind: "text";
			text: string;
			sticker?: string;
	  }
	| {
			kind: "text_template";
			template: string;
			fields: Record<string, string>;
			sticker?: string;
	  }
	| {
			kind: "image_template";
			template: string;
			fields: Record<string, unknown>;
			sticker?: string;
	  }
	| {
			kind: "image";
			paths: string[];
			caption?: string;
	  }
	| {
			kind: "sticker";
			sticker: string;
	  }
	| {
			kind: "file";
			path: string;
			caption?: string;
	  };

/** Session state change reported by the AI (ARCHITECTURE §6 `Transition`). */
export interface Transition {
	state: string;
	data?: Record<string, string>;
}

/**
 * What `chat_reply` hands to `alicedev-reply --json`: a reply, a transition, or both.
 * The union makes "neither" unrepresentable.
 */
export type ReplyRequest =
	| { reply: ReplyPayload; transition?: Transition }
	| { reply?: ReplyPayload; transition: Transition };

export type ReplyStatus = "queued" | "replayed";

export interface ReplySuccess {
	status: ReplyStatus;
}

export function parseReplySuccess(value: unknown): ReplySuccess | undefined {
	if (!isRecord(value)) return undefined;
	const status = value.status;
	if (status !== "queued" && status !== "replayed") return undefined;
	return { status };
}

function fail(error: string, extra?: Record<string, unknown>): never {
	throw new Error(JSON.stringify({ error, ...extra }));
}

function optionalString(value: unknown, error: string): string | undefined {
	if (value === undefined) return undefined;
	if (typeof value !== "string") fail(error);
	return value;
}

function isStringRecord(value: unknown): value is Record<string, string> {
	if (!isRecord(value)) return false;
	return Object.values(value).every(item => typeof item === "string");
}

/** Boundary parser for the model-supplied `reply` object. Throws a JSON error message. */
export function parseReplyPayload(value: unknown): ReplyPayload {
	if (!isRecord(value) || typeof value.kind !== "string") fail("invalid_reply_payload");

	switch (value.kind) {
		case "text": {
			if (typeof value.text !== "string") fail("text_required");
			const sticker = optionalString(value.sticker, "sticker_must_be_string");
			return sticker === undefined ? { kind: "text", text: value.text } : { kind: "text", text: value.text, sticker };
		}
		case "text_template": {
			if (typeof value.template !== "string" || !isStringRecord(value.fields)) {
				fail("template_and_string_fields_required");
			}
			const sticker = optionalString(value.sticker, "sticker_must_be_string");
			const base = { kind: "text_template" as const, template: value.template, fields: value.fields };
			return sticker === undefined ? base : { ...base, sticker };
		}
		case "image_template": {
			if (typeof value.template !== "string" || !isRecord(value.fields)) fail("template_and_fields_required");
			const sticker = optionalString(value.sticker, "sticker_must_be_string");
			const base = { kind: "image_template" as const, template: value.template, fields: value.fields };
			return sticker === undefined ? base : { ...base, sticker };
		}
		case "image": {
			const paths = value.paths;
			if (!Array.isArray(paths) || paths.length === 0 || !paths.every(p => typeof p === "string" && p.length > 0)) {
				fail("paths_required");
			}
			const caption = optionalString(value.caption, "caption_must_be_string");
			const base = { kind: "image" as const, paths: paths as string[] };
			return caption === undefined ? base : { ...base, caption };
		}
		case "sticker":
			if (typeof value.sticker !== "string") fail("sticker_required");
			return { kind: "sticker", sticker: value.sticker };
		case "file": {
			if (typeof value.path !== "string") fail("path_required");
			const caption = optionalString(value.caption, "caption_must_be_string");
			return caption === undefined ? { kind: "file", path: value.path } : { kind: "file", path: value.path, caption };
		}
		default:
			fail("unsupported_reply_kind", { kind: value.kind });
	}
}

/** Boundary parser for the model-supplied `transition` object. */
export function parseTransition(value: unknown): Transition {
	if (!isRecord(value) || typeof value.state !== "string" || value.state.trim().length === 0) {
		fail("transition_state_required");
	}
	if (value.data === undefined) return { state: value.state };
	if (!isStringRecord(value.data)) fail("transition_data_must_be_string_map");
	return { state: value.state, data: value.data };
}

/** Parse `{reply?, transition?}`; at least one must be present. */
export function parseReplyRequest(value: unknown): ReplyRequest {
	if (!isRecord(value)) fail("invalid_request");
	const reply = value.reply === undefined ? undefined : parseReplyPayload(value.reply);
	const transition = value.transition === undefined ? undefined : parseTransition(value.transition);
	if (reply !== undefined && transition !== undefined) return { reply, transition };
	if (reply !== undefined) return { reply };
	if (transition !== undefined) return { transition };
	return fail("reply_or_transition_required");
}
