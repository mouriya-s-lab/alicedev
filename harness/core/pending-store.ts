import type { DurableEntry } from "./durable.js";

export interface PendingMessage {
	readonly msg: string;
	readonly text: string;
}

function stableSignature(msgs: readonly string[]): string {
	return JSON.stringify([...new Set(msgs)].sort());
}

function randomUuidV4(): string {
	const globalObject = globalThis as { crypto?: { randomUUID?: () => string } };
	const generated = globalObject.crypto?.randomUUID?.();
	if (generated !== undefined) return generated;

	const bytes = new Uint8Array(16);
	for (let index = 0; index < bytes.length; index += 1) {
		bytes[index] = Math.floor(Math.random() * 256);
	}
	bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
	bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
	const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
	return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export class PendingStore {
	#pending = new Map<string, string>();
	#reminded = new Set<string>();
	#replyIds = new Map<string, string>();

	static fromEntries(entries: readonly DurableEntry[]): PendingStore {
		const store = new PendingStore();
		store.restore(entries);
		return store;
	}

	add(msg: string, text: string): void {
		if (msg.trim().length === 0) throw new Error("Pending message id must be non-empty");
		if (typeof text !== "string") throw new Error("Pending message text must be a string");
		if (!this.#pending.has(msg)) this.#pending.set(msg, text);
	}

	currentMsgs(): string[] {
		return [...this.#pending.keys()];
	}

	current(): PendingMessage[] {
		return [...this.#pending].map(([msg, text]) => ({ msg, text }));
	}

	textsFor(msgs: readonly string[]): string[] {
		return msgs.map(msg => this.#pending.get(msg) ?? "");
	}

	markConsumed(msgs: readonly string[]): void {
		for (const msg of msgs) this.#pending.delete(msg);
		this.#replyIds.clear();
	}

	markReminded(msgs: readonly string[]): void {
		if (msgs.length > 0) this.#reminded.add(stableSignature(msgs));
	}

	wasReminded(msgs: readonly string[]): boolean {
		return msgs.length > 0 && this.#reminded.has(stableSignature(msgs));
	}

	replyIdFor(msgs: readonly string[]): string {
		const signature = stableSignature(msgs);
		const existing = this.#replyIds.get(signature);
		if (existing !== undefined) return existing;
		const replyId = randomUuidV4();
		this.#replyIds.set(signature, replyId);
		return replyId;
	}

	restore(entries: readonly DurableEntry[]): void {
		this.clear();
		for (const entry of entries) {
			switch (entry.customType) {
				case "alicedev.pending":
					this.add(entry.data.msg, entry.data.text);
					break;
				case "alicedev.consumed":
					this.markConsumed(entry.data.msgs);
					break;
				case "alicedev.reminded":
					this.markReminded(entry.data.msgs);
					break;
				default:
					assertNever(entry);
			}
		}
	}

	clear(): void {
		this.#pending.clear();
		this.#reminded.clear();
		this.#replyIds.clear();
	}
}

function assertNever(value: never): never {
	throw new Error(`Unexpected durable entry: ${JSON.stringify(value)}`);
}

export function pendingSetSignature(msgs: readonly string[]): string {
	return stableSignature(msgs);
}
