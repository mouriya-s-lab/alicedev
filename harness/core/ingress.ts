import { isRecord } from "./guards.js";

export interface IngressMessage {
	readonly session: string;
	readonly msg: string;
	readonly text: string;
}

function requiredString(value: unknown, field: keyof IngressMessage): string {
	if (typeof value !== "string" || value.trim().length === 0) {
		throw new Error(`Invalid chat ingress: ${field} must be a non-empty string`);
	}
	return value;
}


export function parseIngress(raw: string): IngressMessage {
	let value: unknown;
	try {
		value = JSON.parse(raw);
	} catch (error: unknown) {
		const detail = error instanceof Error ? error.message : "invalid JSON";
		throw new Error(`Invalid chat ingress JSON: ${detail}`);
	}
	if (!isRecord(value)) throw new Error("Invalid chat ingress: expected a JSON object");

	return {
		session: requiredString(value.session, "session"),
		msg: requiredString(value.msg, "msg"),
		text: requiredString(value.text, "text"),
	};
}
