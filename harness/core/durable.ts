import { isRecord } from "./guards.js";

export const PENDING_ENTRY_TYPE = "alicedev.pending" as const;
export const CONSUMED_ENTRY_TYPE = "alicedev.consumed" as const;
export const REMINDED_ENTRY_TYPE = "alicedev.reminded" as const;

export interface PendingEntryData {
	readonly agent: string;
	readonly msg: string;
	readonly text: string;
}

export interface ConsumedEntryData {
	readonly msgs: string[];
	readonly reply_id: string;
}

export interface RemindedEntryData {
	readonly msgs: string[];
}

export interface PendingDurableEntry {
	readonly customType: typeof PENDING_ENTRY_TYPE;
	readonly data: PendingEntryData;
}

export interface ConsumedDurableEntry {
	readonly customType: typeof CONSUMED_ENTRY_TYPE;
	readonly data: ConsumedEntryData;
}

export interface RemindedDurableEntry {
	readonly customType: typeof REMINDED_ENTRY_TYPE;
	readonly data: RemindedEntryData;
}

export type DurableEntry = PendingDurableEntry | ConsumedDurableEntry | RemindedDurableEntry;

function assertNonEmptyString(value: string, field: string): string {
	if (value.trim().length === 0) throw new Error(`Invalid ${field}: expected a non-empty string`);
	return value;
}

function copyMessages(msgs: readonly string[]): string[] {
	return msgs.map((msg, index) => assertNonEmptyString(msg, `msgs[${index}]`));
}

export function serializePendingEntry(agent: string, msg: string, text: string): PendingDurableEntry {
	return {
		customType: PENDING_ENTRY_TYPE,
		data: {
			agent: assertNonEmptyString(agent, "agent"),
			msg: assertNonEmptyString(msg, "msg"),
			text,
		},
	};
}

export function serializeConsumedEntry(msgs: readonly string[], replyId: string): ConsumedDurableEntry {
	return {
		customType: CONSUMED_ENTRY_TYPE,
		data: {
			msgs: copyMessages(msgs),
			reply_id: assertNonEmptyString(replyId, "reply_id"),
		},
	};
}

export function serializeRemindedEntry(msgs: readonly string[]): RemindedDurableEntry {
	return {
		customType: REMINDED_ENTRY_TYPE,
		data: { msgs: copyMessages(msgs) },
	};
}

function stringArray(value: unknown): string[] | undefined {
	if (!Array.isArray(value)) return undefined;
	const result: string[] = [];
	for (const item of value) {
		if (typeof item !== "string" || item.trim().length === 0) return undefined;
		result.push(item);
	}
	return result;
}

export function parseDurableEntry(customType: string, value: unknown): DurableEntry | undefined {
	if (!isRecord(value)) return undefined;

	switch (customType) {
		case PENDING_ENTRY_TYPE: {
			// Entries written before the agent rename carry the same ref under `session`.
			const agent = typeof value.agent === "string" ? value.agent : value.session;
			if (typeof agent !== "string" || agent.trim().length === 0) return undefined;
			if (typeof value.msg !== "string" || value.msg.trim().length === 0) return undefined;
			if (value.text !== undefined && typeof value.text !== "string") return undefined;
			return {
				customType: PENDING_ENTRY_TYPE,
				data: { agent, msg: value.msg, text: typeof value.text === "string" ? value.text : "" },
			};
		}
		case CONSUMED_ENTRY_TYPE: {
			const msgs = stringArray(value.msgs);
			if (msgs === undefined || typeof value.reply_id !== "string" || value.reply_id.trim().length === 0) {
				return undefined;
			}
			return { customType: CONSUMED_ENTRY_TYPE, data: { msgs, reply_id: value.reply_id } };
		}
		case REMINDED_ENTRY_TYPE: {
			const msgs = stringArray(value.msgs);
			return msgs === undefined ? undefined : { customType: REMINDED_ENTRY_TYPE, data: { msgs } };
		}
		default:
			return undefined;
	}
}
