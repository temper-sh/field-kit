/**
 * extract_json — text or document in, schema-valid JSON out.
 *
 * Runs NuExtract3 (a 4B extraction VLM) as a typed local function behind a Pi
 * tool. The model is never exposed in Pi's model picker; the main model reaches
 * it only through this tool, and only ever sees the stable contract below.
 *
 * Two things make the output trustworthy:
 *
 *   1. Constrained decoding. The request carries `response_format:
 *      { type: "json_schema" }`, so llama.cpp compiles the schema to a grammar
 *      and the sampler cannot emit a token that would break it. Unparseable
 *      JSON is impossible by construction — there is no repair loop here.
 *   2. Post-hoc invariants. Constrained decoding guarantees *shape*, not
 *      *truth*. Fields the schema marks as extractive are checked to actually
 *      occur in the source text, which is what catches a confident hallucination.
 *
 * NuExtract does not consume JSON Schema directly — it wants its own template
 * dialect, where each leaf is a type name ("verbatim-string", "date", ...) and
 * arrays/enums are written structurally. That conversion lives in this file so
 * callers never have to know about it.
 *
 * Requires: llama-swap on :8080 with an `extract-nuextract3` model entry.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { Type } from "typebox";

const BASE_URL = process.env.LOCAL_AI_BASE_URL ?? "http://localhost:8080/v1";
const MODEL = process.env.LOCAL_AI_EXTRACT_MODEL ?? "extract-nuextract3";
const REQUEST_TIMEOUT_MS = 180_000;
const MAX_SOURCE_CHARS = 200_000;

/** Leaf type names NuExtract understands (TYPES.md). */
const NUEXTRACT_LEAF_TYPES = new Set([
	"integer", "number", "string", "verbatim-string", "date", "time", "date-time",
	"duration", "boolean", "country", "currency", "language", "language-tag",
	"script", "url", "email-address", "phone-number", "iban", "bic", "unit-code",
]);

/** JSON Schema `format` -> NuExtract leaf type. */
const FORMAT_TO_LEAF: Record<string, string> = {
	date: "date",
	time: "time",
	"date-time": "date-time",
	duration: "duration",
	email: "email-address",
	"idn-email": "email-address",
	uri: "url",
	iri: "url",
	url: "url",
	hostname: "url",
};

type Json = unknown;
type SchemaNode = Record<string, any>;

// ── schema -> NuExtract template ─────────────────────────────────────────────

/**
 * A JSON Schema node becomes a template node of identical shape whose leaves
 * name types. `x-nuextract` on any node is an escape hatch that wins outright —
 * use it to ask for "string" (paraphrase allowed) where the default would pick
 * "verbatim-string" (must appear literally in the source).
 */
function schemaToTemplate(node: SchemaNode, path: string, errors: string[]): Json {
	if (!node || typeof node !== "object") {
		errors.push(`${path}: expected a schema object`);
		return "string";
	}

	const override = node["x-nuextract"];
	if (typeof override === "string") {
		if (!NUEXTRACT_LEAF_TYPES.has(override) && !override.startsWith("region:")) {
			errors.push(`${path}: unknown x-nuextract type "${override}"`);
		}
		return override;
	}

	if (Array.isArray(node.enum)) {
		if (node.enum.length === 0) errors.push(`${path}: enum is empty`);
		return node.enum;
	}

	// anyOf/oneOf are commonly just "T or null" — collapse to the non-null branch.
	for (const key of ["anyOf", "oneOf"] as const) {
		if (Array.isArray(node[key])) {
			const branches = node[key].filter((b: SchemaNode) => b?.type !== "null");
			if (branches.length === 1) return schemaToTemplate(branches[0], path, errors);
			errors.push(`${path}: ${key} with multiple non-null branches is not supported`);
			return "string";
		}
	}

	const type = Array.isArray(node.type)
		? node.type.find((t: string) => t !== "null")
		: node.type;

	switch (type) {
		case "object": {
			const props = node.properties ?? {};
			const keys = Object.keys(props);
			if (keys.length === 0) {
				errors.push(`${path}: object has no properties — nothing to extract`);
			}
			const out: Record<string, Json> = {};
			for (const key of keys) {
				out[key] = schemaToTemplate(props[key], `${path}.${key}`, errors);
			}
			return out;
		}
		case "array": {
			if (!node.items) {
				errors.push(`${path}: array has no items schema`);
				return ["string"];
			}
			return [schemaToTemplate(node.items, `${path}[]`, errors)];
		}
		case "integer":
			return "integer";
		case "number":
			return "number";
		case "boolean":
			return "boolean";
		case "string": {
			const fmt = typeof node.format === "string" ? FORMAT_TO_LEAF[node.format] : undefined;
			// Default to verbatim: this tool exists to pull facts out of a document,
			// and a verbatim leaf is one we can verify against the source afterwards.
			return fmt ?? "verbatim-string";
		}
		default:
			errors.push(`${path}: unsupported or missing "type"`);
			return "string";
	}
}

// ── a small structural validator ─────────────────────────────────────────────
// Deliberately a subset: constrained decoding already guarantees conformance,
// so this exists to catch a server that ignored response_format, not to be a
// general-purpose JSON Schema implementation (which would need a dependency).

function validate(value: Json, node: SchemaNode, path: string, errors: string[]): void {
	if (!node || typeof node !== "object") return;
	if (value === null || value === undefined) return; // absent fields are legal

	if (Array.isArray(node.enum)) {
		if (!node.enum.includes(value as never)) {
			errors.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(node.enum)}`);
		}
		return;
	}

	for (const key of ["anyOf", "oneOf"] as const) {
		if (Array.isArray(node[key])) {
			const branches = node[key].filter((b: SchemaNode) => b?.type !== "null");
			if (branches.length === 1) validate(value, branches[0], path, errors);
			return;
		}
	}

	const type = Array.isArray(node.type)
		? node.type.find((t: string) => t !== "null")
		: node.type;

	switch (type) {
		case "object": {
			if (typeof value !== "object" || Array.isArray(value)) {
				errors.push(`${path}: expected object, got ${describe(value)}`);
				return;
			}
			const obj = value as Record<string, Json>;
			for (const req of node.required ?? []) {
				if (!(req in obj)) errors.push(`${path}.${req}: required property is missing`);
			}
			for (const [key, sub] of Object.entries(node.properties ?? {})) {
				if (key in obj) validate(obj[key], sub as SchemaNode, `${path}.${key}`, errors);
			}
			return;
		}
		case "array": {
			if (!Array.isArray(value)) {
				errors.push(`${path}: expected array, got ${describe(value)}`);
				return;
			}
			if (node.items) {
				value.forEach((item, i) => validate(item, node.items, `${path}[${i}]`, errors));
			}
			return;
		}
		case "integer":
			if (typeof value !== "number" || !Number.isInteger(value)) {
				errors.push(`${path}: expected integer, got ${describe(value)}`);
			}
			return;
		case "number":
			if (typeof value !== "number") errors.push(`${path}: expected number, got ${describe(value)}`);
			return;
		case "boolean":
			if (typeof value !== "boolean") errors.push(`${path}: expected boolean, got ${describe(value)}`);
			return;
		case "string":
			if (typeof value !== "string") errors.push(`${path}: expected string, got ${describe(value)}`);
			return;
	}
}

function describe(value: Json): string {
	if (value === null) return "null";
	if (Array.isArray(value)) return "array";
	return typeof value;
}

// ── invariants ───────────────────────────────────────────────────────────────

/** NuExtract normalizes runs of whitespace to a single space in verbatim fields. */
function normalizeWs(s: string): string {
	return s.replace(/\s+/g, " ").trim();
}

/**
 * Walk the template and the extracted value together. Every leaf the template
 * marked "verbatim-string" must actually occur in the source — that is the
 * check that separates an extraction from an invention.
 */
function checkVerbatimSpans(
	value: Json,
	template: Json,
	path: string,
	haystack: string,
	errors: string[],
): void {
	if (value === null || value === undefined) return;

	if (template === "verbatim-string") {
		if (typeof value === "string" && value.length > 0) {
			if (!haystack.includes(normalizeWs(value))) {
				errors.push(`${path}: ${JSON.stringify(value)} does not appear in the source text`);
			}
		}
		return;
	}

	if (Array.isArray(template)) {
		// A template array is either [itemTemplate] or an enum list. Only the
		// former describes a repeated structure.
		if (template.length === 1 && Array.isArray(value)) {
			value.forEach((item, i) =>
				checkVerbatimSpans(item, template[0], `${path}[${i}]`, haystack, errors),
			);
		}
		return;
	}

	if (template && typeof template === "object" && value && typeof value === "object") {
		for (const [key, sub] of Object.entries(template as Record<string, Json>)) {
			checkVerbatimSpans((value as Record<string, Json>)[key], sub, `${path}.${key}`, haystack, errors);
		}
	}
}

/** Report extracted values that look like local paths but do not resolve. */
function checkReferencedFiles(value: Json, baseDir: string, path: string, warnings: string[]): void {
	if (typeof value === "string") {
		const looksLikePath = isAbsolute(value) || value.startsWith("./") || value.startsWith("../");
		if (looksLikePath && !value.includes("\n") && value.length < 4096) {
			const target = isAbsolute(value) ? value : resolve(baseDir, value);
			if (!existsSync(target)) warnings.push(`${path}: referenced path does not exist: ${value}`);
		}
		return;
	}
	if (Array.isArray(value)) {
		value.forEach((v, i) => checkReferencedFiles(v, baseDir, `${path}[${i}]`, warnings));
		return;
	}
	if (value && typeof value === "object") {
		for (const [k, v] of Object.entries(value as Record<string, Json>)) {
			checkReferencedFiles(v, baseDir, `${path}.${k}`, warnings);
		}
	}
}

// ── tool ─────────────────────────────────────────────────────────────────────

const PARAMS = Type.Object({
	text_or_path: Type.String({
		description:
			"The source to extract from: either the literal text, or a path to a UTF-8 text file. " +
			"A value that resolves to an existing file is read; anything else is treated as text.",
	}),
	schema: Type.Object(
		{},
		{
			additionalProperties: true,
			description:
				"JSON Schema for the object to extract. Supported: object/array/string/number/" +
				"integer/boolean, enum, required, and string format (date, date-time, time, " +
				"duration, email, uri). Strings are treated as strictly extractive and are " +
				'verified against the source; set "x-nuextract": "string" on a property to ' +
				"allow paraphrase or inference instead.",
		},
	),
});

export default function extractJsonExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "extract_json",
		label: "Extract JSON",
		description:
			"Extract structured data from text or a text file into JSON matching a schema you " +
			"supply. Runs a local extraction model with constrained decoding, so the result " +
			"always parses and always matches the schema. Prefer this over eyeballing a " +
			"document yourself when you need specific fields out of unstructured text.",
		promptSnippet: "Pull schema-shaped JSON out of unstructured text or a document",
		promptGuidelines: [
			"Use extract_json when you need specific fields from unstructured text — invoices, logs, changelogs, READMEs, scraped pages.",
			"Describe exactly the fields you want; a smaller schema extracts more reliably than a kitchen-sink one.",
			"String fields must appear verbatim in the source. If you want a summary or an inferred value, mark that property with \"x-nuextract\": \"string\".",
			"The tool returns an error rather than guessing. If it reports fields missing from the source, that is a real signal about the document, not a retry prompt.",
		],
		parameters: PARAMS,
		async execute(_toolCallId, params, signal) {
			const { text_or_path: source, schema } = params as {
				text_or_path: string;
				schema: SchemaNode;
			};

			// ── resolve the source ───────────────────────────────────────────
			let text = source;
			let sourcePath: string | undefined;
			try {
				if (source.length < 4096 && !source.includes("\n") && existsSync(source) && statSync(source).isFile()) {
					sourcePath = resolve(source);
					text = await readFile(sourcePath, "utf8");
				}
			} catch {
				// Unreadable path — fall through and treat the argument as literal text.
			}

			if (text.trim().length === 0) {
				return errorResult("The source is empty — nothing to extract.", { sourcePath });
			}
			if (text.length > MAX_SOURCE_CHARS) {
				return errorResult(
					`The source is ${text.length} characters, over the ${MAX_SOURCE_CHARS} limit. ` +
						"Extract from a smaller slice, or split the document and merge the results.",
					{ sourcePath, length: text.length },
				);
			}

			// ── schema -> template ───────────────────────────────────────────
			const schemaErrors: string[] = [];
			const template = schemaToTemplate(schema, "$", schemaErrors);
			if (schemaErrors.length > 0) {
				return errorResult(
					"The schema cannot be expressed as an extraction template:\n" +
						schemaErrors.map((e) => `  - ${e}`).join("\n"),
					{ schemaErrors },
				);
			}

			// ── call the model ───────────────────────────────────────────────
			const controller = new AbortController();
			const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
			const onAbort = () => controller.abort();
			signal?.addEventListener("abort", onAbort, { once: true });

			let raw: string;
			let finishReason = "";
			try {
				const res = await fetch(`${BASE_URL}/chat/completions`, {
					method: "POST",
					headers: { "Content-Type": "application/json", Authorization: "Bearer local" },
					signal: controller.signal,
					body: JSON.stringify({
						model: MODEL,
						temperature: 0.2,
						max_tokens: 4096,
						messages: [{ role: "user", content: [{ type: "text", text }] }],
						// Hard structural guarantee, independent of the chat template.
						response_format: {
							type: "json_schema",
							json_schema: { name: "extraction", schema, strict: true },
						},
						// NuExtract's native extraction path.
						chat_template_kwargs: {
							template: JSON.stringify(template, null, 4),
							enable_thinking: false,
						},
					}),
				});

				if (!res.ok) {
					const body = await res.text().catch(() => "");
					return errorResult(
						`The extraction model returned HTTP ${res.status}. ` +
							`Check that llama-swap is running and "${MODEL}" is in its config.\n${body.slice(0, 500)}`,
						{ status: res.status, model: MODEL, baseUrl: BASE_URL },
					);
				}

				const payload = (await res.json()) as any;
				raw = payload?.choices?.[0]?.message?.content ?? "";
				finishReason = payload?.choices?.[0]?.finish_reason ?? "";
			} catch (err) {
				const aborted = (err as Error)?.name === "AbortError";
				return errorResult(
					aborted
						? `The extraction model did not answer within ${REQUEST_TIMEOUT_MS / 1000}s.`
						: `Could not reach the extraction model at ${BASE_URL}: ${(err as Error).message}`,
					{ model: MODEL, baseUrl: BASE_URL },
				);
			} finally {
				clearTimeout(timer);
				signal?.removeEventListener("abort", onAbort);
			}

			// ── parse ────────────────────────────────────────────────────────
			let value: Json;
			try {
				value = JSON.parse(raw);
			} catch {
				// Truncation looks exactly like a grammar failure from here: the
				// output is unparseable either way. It is not the server's fault
				// though, and telling someone to check their llama-server build
				// when they actually need a bigger max_tokens sends them a long
				// way in the wrong direction. finish_reason distinguishes them.
				if (finishReason === "length") {
					return errorResult(
						"The extraction model hit its token limit before finishing the JSON " +
							`(finish_reason: length, ${raw.length} characters produced). The output is ` +
							"valid as far as it goes — this is a truncation, not a grammar failure. " +
							"Extract fewer fields at once, or raise the response limit.",
						{ finishReason, rawPreview: raw.slice(0, 500), model: MODEL },
					);
				}
				// Only reachable if the server ignored response_format.
				return errorResult(
					"The extraction model returned text that is not JSON, which means constrained " +
						"decoding did not apply. Check that llama-swap routes to a llama-server build " +
						"that supports response_format: json_schema.",
					{ finishReason, rawPreview: raw.slice(0, 500) },
				);
			}

			// ── validate ─────────────────────────────────────────────────────
			const errors: string[] = [];
			validate(value, schema, "$", errors);

			const haystack = normalizeWs(text);
			checkVerbatimSpans(value, template, "$", haystack, errors);

			const warnings: string[] = [];
			checkReferencedFiles(value, sourcePath ? dirname(sourcePath) : process.cwd(), "$", warnings);

			if (errors.length > 0) {
				return errorResult(
					"The extraction did not pass validation:\n" +
						errors.map((e) => `  - ${e}`).join("\n") +
						"\n\nThe partial result is below. Fields flagged as not appearing in the source " +
						"were invented by the model, not found in the document.\n\n" +
						JSON.stringify(value, null, 2),
					{ errors, warnings, value, sourcePath },
				);
			}

			const parts = [JSON.stringify(value, null, 2)];
			if (warnings.length > 0) {
				parts.push("", "Warnings:", ...warnings.map((w) => `  - ${w}`));
			}

			return {
				content: [{ type: "text", text: parts.join("\n") }],
				details: { value, warnings, sourcePath, model: MODEL },
			};
		},
	});
}

function errorResult(message: string, details: Record<string, Json>) {
	return {
		content: [{ type: "text" as const, text: message }],
		details,
		isError: true,
	};
}
