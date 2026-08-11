/**
 * compact-test-output — the only thing shrinking command output on this stack.
 *
 * This was written as a safety net behind `rtk`, on the assumption that `rtk`
 * did the real work at the command level and this caught the leftovers. That
 * assumption was measured on 2026-07-31 and is false in both halves:
 *
 *   - `--pi-hints` has never been on. `PI_HINTS=0` is the default
 *     (`setup.sh:29`) and `~/.pi/agent/APPEND_SYSTEM.md` does not exist here,
 *     so the steer has never reached a system prompt.
 *   - Turning it on would not help. With the hint present, the agent invoked
 *     `rtk` in 0 of 4 runs. Suggesting a command to a 27B model does not make
 *     it type one.
 *
 * So there is nothing upstream of this extension. It is not a backstop, it is
 * the mechanism, and its narrow trigger carries more weight than intended.
 * Before widening it, read docs/DECISIONS.md: all tool output across
 * 16 runs came to ~19s of prefill against a 173s mean run, so the ceiling on
 * output compression here is about 11% of wall-clock, and unconditionally
 * routing commands through a compressor measurably *loses* — the agent scopes
 * `ls -laR` down to ~100 bytes where `rtk` returns 16,880.
 *
 * `rtk` itself stays installed and is still the right tool by hand. The thing
 * that does not work is expecting the model to reach for it.
 *
 * The trigger is deliberately narrow. It fires only when all of these hold:
 *
 *   - the result came from the bash tool,
 *   - the command was NOT run through rtk,
 *   - the output is longer than ~100 lines OR ~8KB — the byte half matters
 *     because `jest --json` and `eslint -f json` put everything on one line,
 *     so a line-only gate never fired on the very formats the deterministic
 *     parsers were written for, and
 *   - the command looks like a test/lint/build runner, or the output parses as
 *     a format one of them emits. "Looks like a runner" means the runner is the
 *     *program* of some segment, not merely a word somewhere on the line:
 *     `cat pytest-output.log` is not a test run.
 *
 * That last condition matters: a large `cat`, `git log` or `find` is not test
 * output, and silently summarizing it would lose information the agent asked
 * for on purpose.
 *
 * Recognized machine formats (eslint/rubocop/rspec/jest JSON, junit/pytest XML)
 * get plain deterministic parsers. Only unrecognized freeform output falls back
 * to the local extraction model, and if that call fails the extension degrades
 * to head/tail truncation rather than failing the tool result.
 *
 * The full raw output is always written to a file and its path included, so
 * nothing is ever actually lost — the agent can go read it.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isBashToolResult } from "@earendil-works/pi-coding-agent";
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE_URL = process.env.LOCAL_AI_BASE_URL ?? "http://localhost:8080/v1";
const MODEL = process.env.LOCAL_AI_EXTRACT_MODEL ?? "extract-nuextract3";

const LINE_THRESHOLD = 100;
/**
 * The line gate alone never fired on the formats the deterministic parsers
 * exist for: `jest --json` and `eslint -f json` emit everything on a single
 * line, so a 400KB result counted as one line and passed straight through.
 * Either dimension being large is enough to be worth compacting.
 */
const BYTE_THRESHOLD = 8_000;
const MODEL_TIMEOUT_MS = 60_000;
/** How much freeform output to hand the extraction model. */
const MODEL_INPUT_CHARS = 24_000;
/** Head/tail sizes for the deterministic last-resort truncation. */
const FALLBACK_HEAD = 40;
const FALLBACK_TAIL = 60;

/**
 * Commands whose output is worth compacting.
 *
 * Anchored, and matched against the *program* of a segment rather than against
 * the whole command line. Unanchored, `cat pytest-output.log` and
 * `grep -r jest .` both classified as runners, and their output — which the
 * agent asked for on purpose — got summarized away.
 */
const RUNNER_PATTERN =
	/^(?:\S*\/)?(pytest|py\.test|tox|nox|unittest|jest|vitest|mocha|jasmine|karma|ava|rspec|rake\s+spec|rubocop|minitest|eslint|biome|oxlint|tsc|standardrb|phpunit|pest|go\s+test|gotestsum|cargo\s+(test|clippy)|ctest|mvn|gradle|dotnet\s+test|swift\s+test|npm\s+(run\s+)?(test|lint)|pnpm\s+(run\s+)?(test|lint)|yarn\s+(test|lint)|bun\s+test|make\s+(test|check|lint))\b/;

/** `FOO=1 BAR=2 cmd` — leading environment assignments. */
const ENV_ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=\S*\s*/;
/**
 * Programs that take the real command as their arguments, along with their own
 * flag/duration arguments: `time -p pytest`, `timeout 30 jest`, `nice -n 10 …`.
 */
const WRAPPER = /^(?:env|time|nice|ionice|nohup|stdbuf|command|exec|timeout)\b(?:\s+-\S+|\s+\d+[smhd]?)*\s*/;

/** Split a command line into the segments that each run a program. */
export function commandSegments(command: string): string[] {
	return command
		.split(/\|\||&&|[|;&\n]/)
		.map((s) => s.trim())
		.filter(Boolean);
}

/** Strip env assignments and wrapper programs to reach the program itself. */
export function programOf(segment: string): string {
	let s = segment.trim();
	// Bounded: each pass must consume something or the loop stops.
	for (let i = 0; i < 8; i++) {
		const next = s.replace(ENV_ASSIGN, "").replace(WRAPPER, "");
		if (next === s) break;
		s = next;
	}
	return s;
}

/** Does any segment of this command line actually run a test/lint runner? */
export function looksLikeRunner(command: string): boolean {
	return commandSegments(command).some((seg) => RUNNER_PATTERN.test(programOf(seg)));
}

/**
 * Is this command already run through rtk?
 *
 * The old test required rtk to sit at the start of the line or straight after a
 * `|`/`;`/`&`, so `FOO=1 rtk pytest` and `time rtk pytest` both read as *not*
 * using rtk and got compacted a second time.
 */
export function usesRtk(command: string): boolean {
	return commandSegments(command).some((seg) => /^(?:\S*\/)?rtk\b/.test(programOf(seg)));
}

type Json = any;

interface Finding {
	name: string;
	message?: string;
	location?: string;
}

interface Summary {
	status: string;
	category: string;
	findings: Finding[];
	keyLines?: string[];
	note?: string;
}

// ── deterministic parsers ────────────────────────────────────────────────────

export function parseEslint(doc: Json): Summary | undefined {
	if (!Array.isArray(doc) || doc.length === 0) return undefined;
	if (!doc.every((f) => f && typeof f.filePath === "string" && Array.isArray(f.messages))) return undefined;

	const findings: Finding[] = [];
	let errors = 0;
	let warnings = 0;
	for (const file of doc) {
		for (const m of file.messages) {
			if (m.severity === 2) errors++;
			else warnings++;
			if (m.severity === 2 && findings.length < 40) {
				findings.push({
					name: m.ruleId ?? "error",
					message: m.message,
					location: `${file.filePath}:${m.line ?? 0}:${m.column ?? 0}`,
				});
			}
		}
	}
	return {
		status: errors > 0 ? "failed" : warnings > 0 ? "passed with warnings" : "passed",
		category: "eslint",
		findings,
		note: `${errors} error(s), ${warnings} warning(s) across ${doc.length} file(s)`,
	};
}

export function parseRubocop(doc: Json): Summary | undefined {
	if (!doc?.summary || !Array.isArray(doc.files)) return undefined;
	const findings: Finding[] = [];
	for (const file of doc.files) {
		for (const o of file.offenses ?? []) {
			if (findings.length >= 40) break;
			findings.push({
				name: o.cop_name ?? "offense",
				message: o.message,
				location: `${file.path}:${o.location?.line ?? 0}:${o.location?.column ?? 0}`,
			});
		}
	}
	return {
		status: (doc.summary.offense_count ?? 0) > 0 ? "failed" : "passed",
		category: "rubocop",
		findings,
		note: `${doc.summary.offense_count ?? 0} offense(s) in ${doc.summary.inspected_file_count ?? 0} file(s)`,
	};
}

export function parseRspec(doc: Json): Summary | undefined {
	if (!doc?.summary || !Array.isArray(doc.examples)) return undefined;
	const findings: Finding[] = [];
	for (const ex of doc.examples) {
		if (ex.status !== "failed") continue;
		if (findings.length >= 40) break;
		findings.push({
			name: ex.full_description ?? ex.description ?? "example",
			message: firstProjectFrame(ex.exception?.message, ex.exception?.backtrace),
			location: ex.file_path ? `${ex.file_path}:${ex.line_number ?? 0}` : undefined,
		});
	}
	return {
		status: (doc.summary.failure_count ?? 0) > 0 ? "failed" : "passed",
		category: "rspec",
		findings,
		note: doc.summary.line ?? `${doc.summary.example_count} examples, ${doc.summary.failure_count} failures`,
	};
}

export function parseJest(doc: Json): Summary | undefined {
	if (typeof doc?.numTotalTests !== "number" || !Array.isArray(doc.testResults)) return undefined;
	const findings: Finding[] = [];
	for (const suite of doc.testResults) {
		for (const t of suite.assertionResults ?? []) {
			if (t.status !== "failed") continue;
			if (findings.length >= 40) break;
			findings.push({
				name: t.fullName ?? t.title ?? "test",
				message: firstProjectFrame((t.failureMessages ?? []).join("\n")),
				location: suite.name,
			});
		}
	}
	return {
		status: doc.numFailedTests > 0 ? "failed" : "passed",
		category: "jest",
		findings,
		note: `${doc.numPassedTests}/${doc.numTotalTests} passed, ${doc.numFailedTests} failed`,
	};
}

/** junit / pytest XML, parsed with regex — no XML dependency. */
export function parseJunitXml(text: string): Summary | undefined {
	if (!/<testsuites?\b/.test(text)) return undefined;

	// \b matters throughout this parser: without it `name="` matches inside
	// `classname="`, and every finding came out named after its class twice
	// (`tests.test_api.tests.test_api` instead of `tests.test_api.test_create`).
	const attr = (s: string, name: string): number => {
		const m = new RegExp(`\\b${name}="(\\d+)"`).exec(s);
		return m ? Number(m[1]) : 0;
	};
	const header = /<testsuites?\b[^>]*>/.exec(text)?.[0] ?? "";
	let tests = attr(header, "tests");
	let failures = attr(header, "failures");
	let errors = attr(header, "errors");

	// pytest wraps its results in a bare `<testsuites>` with no attributes and
	// puts the counts on the `<testsuite>` inside. Reading only the header gave
	// 0 failures on a failing run, which rendered as "passed" — the worst
	// possible way for this to be wrong. Sum the suites when the header is bare.
	//
	// `<testsuite\b` cannot match `<testsuites` — the trailing "s" is a word
	// character, so there is no boundary there.
	if (tests === 0 && failures === 0 && errors === 0) {
		const suiteRe = /<testsuite\b[^>]*>/g;
		let s: RegExpExecArray | null;
		while ((s = suiteRe.exec(text)) !== null) {
			tests += attr(s[0], "tests");
			failures += attr(s[0], "failures");
			errors += attr(s[0], "errors");
		}
	}

	const findings: Finding[] = [];
	// Passing tests are normally emitted self-closing: `<testcase name="ok"/>`.
	// Treating that as an opening tag made `[\s\S]*?` run past it to the *next*
	// `</testcase>`, so a passing test inherited the following test's <failure>
	// and got reported as the failure. Match both shapes explicitly.
	const caseRe = /<testcase\b([^>]*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g;
	let m: RegExpExecArray | null;
	while ((m = caseRe.exec(text)) !== null && findings.length < 40) {
		const attrs = m[1];
		const body = m[2] ?? "";
		if (!/<(failure|error)\b/.test(body)) continue;
		const name = /\bname="([^"]*)"/.exec(attrs)?.[1] ?? "test";
		const cls = /\bclassname="([^"]*)"/.exec(attrs)?.[1];
		const msg = /<(?:failure|error)\b[^>]*\bmessage="([^"]*)"/.exec(body)?.[1];
		findings.push({
			name: cls ? `${cls}.${name}` : name,
			message: msg ? decodeXml(msg) : undefined,
			location: /\bfile="([^"]*)"/.exec(attrs)?.[1],
		});
	}

	// Belt and braces: a `<failure>` element we actually parsed outranks any
	// count, present or absent. Reporting "passed" above a list of failures is
	// the one outcome that would actively mislead.
	const failed = failures + errors > 0 || findings.length > 0;
	return {
		status: failed ? "failed" : "passed",
		category: "junit-xml",
		findings,
		note:
			tests + failures + errors > 0
				? `${tests} test(s), ${failures} failure(s), ${errors} error(s)`
				: `${findings.length} failure(s) found; the report carried no counts`,
	};
}

export function decodeXml(s: string): string {
	return s
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">")
		.replace(/&quot;/g, '"')
		.replace(/&apos;/g, "'")
		.replace(/&amp;/g, "&");
}

/**
 * Keep the exception message plus the first stack frame that points into the
 * project rather than into a gem/node_modules/stdlib — that frame is nearly
 * always where the fix goes.
 */
export function firstProjectFrame(message?: string, backtrace?: string[]): string | undefined {
	if (!message && !backtrace) return undefined;
	const head = (message ?? "").split("\n").slice(0, 3).join("\n").trim();
	const frames = backtrace ?? (message ?? "").split("\n");
	const project = frames.find(
		(f) =>
			/\.(rb|js|ts|tsx|jsx|py|go|rs|java|kt|swift|php):\d+/.test(f) &&
			!/node_modules|\/gems\/|site-packages|\/vendor\/|\/usr\/lib|\/\.rbenv\//.test(f),
	);
	return project ? `${head}\n  at ${project.trim()}` : head || undefined;
}

export function tryDeterministic(text: string): Summary | undefined {
	const trimmed = text.trim();
	if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
		try {
			const doc = JSON.parse(trimmed);
			return parseEslint(doc) ?? parseRubocop(doc) ?? parseRspec(doc) ?? parseJest(doc);
		} catch {
			// Not JSON after all — fall through.
		}
	}
	return parseJunitXml(trimmed);
}

// ── model fallback ───────────────────────────────────────────────────────────

/** Sent as `response_format` — plain JSON Schema, no local extensions. */
const FALLBACK_SCHEMA = {
	type: "object",
	properties: {
		status: { type: "string", enum: ["passed", "failed", "error", "unknown"] },
		category: { type: "string" },
		summary: { type: "string" },
		failures: {
			type: "array",
			items: {
				type: "object",
				properties: {
					name: { type: "string" },
					message: { type: "string" },
					location: { type: "string" },
				},
				required: ["name"],
			},
		},
	},
	required: ["status", "summary"],
};

/**
 * The same shape in NuExtract's template dialect. `string` rather than
 * `verbatim-string` throughout: a run summary is a derived description, not a
 * span lifted out of the output.
 */
const FALLBACK_TEMPLATE = {
	status: ["passed", "failed", "error", "unknown"],
	category: "string",
	summary: "string",
	failures: [{ name: "string", message: "string", location: "string" }],
};

async function summarizeWithModel(text: string, signal?: AbortSignal): Promise<Summary | undefined> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), MODEL_TIMEOUT_MS);
	const onAbort = () => controller.abort();
	signal?.addEventListener("abort", onAbort, { once: true });

	try {
		const res = await fetch(`${BASE_URL}/chat/completions`, {
			method: "POST",
			headers: { "Content-Type": "application/json", Authorization: "Bearer local" },
			signal: controller.signal,
			body: JSON.stringify({
				model: MODEL,
				temperature: 0,
				max_tokens: 2048,
				messages: [
					{
						role: "user",
						content: [
							{
								type: "text",
								text: `Command output:\n\n${text.slice(0, MODEL_INPUT_CHARS)}`,
							},
						],
					},
				],
				response_format: {
					type: "json_schema",
					json_schema: { name: "run_summary", schema: FALLBACK_SCHEMA, strict: true },
				},
				chat_template_kwargs: {
					template: JSON.stringify(FALLBACK_TEMPLATE, null, 4),
					enable_thinking: false,
				},
			}),
		});
		if (!res.ok) return undefined;

		const payload = (await res.json()) as any;
		const parsed = JSON.parse(payload?.choices?.[0]?.message?.content ?? "");
		return {
			status: parsed.status ?? "unknown",
			category: parsed.category || "command output",
			findings: (parsed.failures ?? []).slice(0, 40),
			note: parsed.summary,
		};
	} catch {
		return undefined;
	} finally {
		clearTimeout(timer);
		signal?.removeEventListener("abort", onAbort);
	}
}

// ── rendering ────────────────────────────────────────────────────────────────

export function render(summary: Summary, rawPath: string, originalSize: string): string {
	const out: string[] = [];
	out.push(`${summary.category}: ${summary.status}`);
	if (summary.note) out.push(summary.note);

	if (summary.findings.length > 0) {
		out.push("");
		out.push(`Failures (${summary.findings.length} shown):`);
		for (const f of summary.findings) {
			out.push(`  - ${f.name}${f.location ? `  [${f.location}]` : ""}`);
			if (f.message) {
				for (const line of f.message.split("\n").slice(0, 3)) {
					out.push(`      ${line.trim()}`);
				}
			}
		}
	}

	if (summary.keyLines?.length) {
		out.push("");
		out.push("Key lines:");
		for (const l of summary.keyLines) out.push(`  ${l}`);
	}

	out.push("");
	out.push(`Compacted from ${originalSize}. Full output: ${rawPath}`);
	return out.join("\n");
}

/** Describe the original size in whichever dimension made it worth compacting. */
export function describeSize(lineCount: number, byteCount: number): string {
	return lineCount > 1 ? `${lineCount} lines` : `${byteCount} bytes`;
}

/**
 * Last resort when nothing could parse it: keep the ends, drop the middle.
 *
 * Splitting by line only works when there are lines to split. A single 400KB
 * JSON line has fewer lines than the head and tail want, which produced both
 * ends identical and a negative "lines omitted" count — so that shape falls
 * back to slicing characters instead.
 */
export function truncateMiddle(lines: string[], rawPath: string): string {
	const text = lines.join("\n");
	const from = describeSize(lines.length, text.length);

	if (lines.length <= FALLBACK_HEAD + FALLBACK_TAIL) {
		const headChars = 2_000;
		const tailChars = 2_000;
		if (text.length <= headChars + tailChars) {
			return `${text}\n\nFull output: ${rawPath}`;
		}
		return [
			text.slice(0, headChars),
			"",
			`  … ${text.length - headChars - tailChars} characters omitted …`,
			"",
			text.slice(-tailChars),
			"",
			`Compacted from ${from}. Full output: ${rawPath}`,
		].join("\n");
	}

	const head = lines.slice(0, FALLBACK_HEAD);
	const tail = lines.slice(-FALLBACK_TAIL);
	return [
		...head,
		"",
		`  … ${lines.length - head.length - tail.length} lines omitted …`,
		"",
		...tail,
		"",
		`Compacted from ${from}. Full output: ${rawPath}`,
	].join("\n");
}

// ── extension ────────────────────────────────────────────────────────────────

export default function compactTestOutputExtension(pi: ExtensionAPI) {
	pi.on("tool_result", async (event, ctx) => {
		if (!isBashToolResult(event)) return undefined;

		const command = String((event.input as any)?.command ?? "");
		if (!command || usesRtk(command)) return undefined;

		// Pull the text out of the result content blocks.
		const blocks = (event.content ?? []) as Array<{ type: string; text?: string }>;
		const text = blocks
			.filter((b) => b.type === "text" && typeof b.text === "string")
			.map((b) => b.text as string)
			.join("\n");
		if (!text) return undefined;

		const lines = text.split("\n");
		if (lines.length <= LINE_THRESHOLD && text.length <= BYTE_THRESHOLD) return undefined;

		const deterministic = tryDeterministic(text);
		if (!deterministic && !looksLikeRunner(command)) {
			// Large, but not a runner and not a recognized format — leave it alone.
			return undefined;
		}

		// Preserve the raw output. Pi may already have spilled it to disk when it
		// truncated; reuse that file rather than writing a second copy.
		let rawPath = (event.details as any)?.fullOutputPath as string | undefined;
		if (!rawPath) {
			rawPath = join(tmpdir(), `pi-output-${event.toolCallId}.log`);
			try {
				writeFileSync(rawPath, text, "utf8");
			} catch {
				return undefined; // Cannot preserve it — better to pass the output through intact.
			}
		}

		const summary = deterministic ?? (await summarizeWithModel(text, ctx.signal));
		const compacted = summary
			? render(summary, rawPath, describeSize(lines.length, text.length))
			: truncateMiddle(lines, rawPath);

		return {
			content: [{ type: "text", text: compacted }],
			details: {
				...(event.details as object),
				compactedBy: "compact-test-output",
				strategy: deterministic ? deterministic.category : summary ? "model" : "truncate",
				originalLines: lines.length,
				fullOutputPath: rawPath,
			},
		};
	});
}
