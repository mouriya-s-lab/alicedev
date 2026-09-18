import { isRecord } from "./guards.js";

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
			kind: "sticker";
			sticker: string;
	  }
	| {
			kind: "file";
			path: string;
			caption?: string;
	  };

export type ReplyStatus = "sent" | "replayed";

export interface ReplySuccess {
	status: ReplyStatus;
}


export function parseReplySuccess(value: unknown): ReplySuccess | undefined {
	if (!isRecord(value)) return undefined;
	const status = value.status;
	if (status !== "sent" && status !== "replayed") return undefined;
	return { status };
}
