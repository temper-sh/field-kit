/**
 * project_search — semantic file search without an index.
 *
 * `rg` finds files whose contents contain useful words from the query, `fd`
 * adds filename matches, and the resident Qwen reranker orders those files by
 * relevance to the full natural-language query. The tool returns five files,
 * not five arbitrary matching lines: its eventual quality gate is whether the
 * file that answers a query appears in the top five.
 *
 * Every query is appended to a private JSONL file. That log is product data,
 * not debug debris: once it contains at least 30 judged queries it becomes the
 * evaluation set for deciding whether this deliberately simple design is good
 * enough or needs the deferred listwise/vector alternatives in docs/PLAN.md.
 *
 * Requires: `rg`, `fd`, and llama-swap on :8080 with a
 * `rerank-qwen3-0.6b` model entry.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { appendFile, mkdir, readFile, realpath, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { Type } from "typebox";

const DEFAULT_BASE_URL = "http://localhost:8080/v1";
const DEFAULT_MODEL = "rerank-qwen3-0.6b";
const MAX_CANDIDATES = 40;
const MAX_RESULTS = 5;
const MAX_TERMS = 10;
const MAX_FILE_BYTES = 1_000_000;
// The serving reranker has a physical batch of 512 tokens. Code is often much
// denser than prose in tokens/character, so stay comfortably below it rather
// than discovering the limit as an HTTP 500 after candidate collection.
const MAX_DOCUMENT_CHARS = 800;
const MAX_LINE_CHARS = 300;
const RERANK_DOCUMENT_LIMITS = [MAX_DOCUMENT_CHARS, 400, 200];
const MAX_COMMAND_OUTPUT_BYTES = 8_000_000;
const DISCOVERY_TIMEOUT_MS = 20_000;
const RERANK_TIMEOUT_MS = 180_000;
const MIN_HEALTHY_SCORE = 0.001;

const EXCLUDED_DIRS = [".git", "node_modules", ".venv", "vendor", "dist", "build", "target", ".next", "coverage"];

const STOP_WORDS = new Set([
	"a", "an", "and", "are", "as", "at", "be", "by", "can", "code", "does", "file",
	"find", "for", "from", "how", "i", "in", "is", "it", "me", "of", "on", "or",
	"please", "search", "show", "that", "the", "this", "to", "where", "which", "with",
	"would", "you",
]);

interface MatchLine {
	line: number;
	text: string;
}

export interface SearchCandidate {
	path: string;
	matches: MatchLine[];
	lexicalScore: number;
	pathMatch: boolean;
}

interface HydratedCandidate extends SearchCandidate {
	document: string;
	excerpt: string;
	locationLine?: number;
}

export interface RankedCandidate extends HydratedCandidate {
	relevanceScore: number;
}

export interface CommandResult {
	stdout: string;
	stderr: string;
	truncated: boolean;
}

type CommandRunner = (
	command: string,
	args: string[],
	cwd: string,
	signal?: AbortSignal,
) => Promise<CommandResult>;

interface SearchDependencies {
	runCommand?: CommandRunner;
	fetch?: typeof fetch;
	logPath?: string;
	now?: () => Date;
}

interface QueryLogRecord {
	schema: 1;
	timestamp: string;
	status: "ok" | "no_candidates" | "error" | "cancelled";
	query: string;
	root: string;
	terms: string[];
	candidates: string[];
	results: Array<{ path: string; score: number }>;
	error?: string;
}

const PARAMS = Type.Object({
	query: Type.String({
		minLength: 1,
		maxLength: 1_000,
		description:
			"What you are trying to locate, in natural language. Include distinctive symbols, " +
			"flags, filenames, or domain words when you know them.",
	}),
	path: Type.Optional(
		Type.String({
			description: "Directory to search, absolute or relative to the current project. Defaults to the project root.",
		}),
	),
});

/** Pull a small, safe set of literal grep terms out of a natural-language query. */
export function queryTerms(query: string): string[] {
	const terms: string[] = [];
	const seen = new Set<string>();

	const add = (raw: string, special = false) => {
		const term = raw
			.normalize("NFKC")
			.replace(/^[^\p{L}\p{N}-]+|[^\p{L}\p{N}_.:/-]+$/gu, "")
			.toLowerCase();
		if (!term || seen.has(term)) return;
		if (!special && (term.length < 2 || STOP_WORDS.has(term))) return;
		seen.add(term);
		terms.push(term);
	};

	// Preserve quoted phrases and code-shaped identifiers before splitting them.
	for (const match of query.matchAll(/["']([^"'\n]{2,80})["']/g)) add(match[1], true);
	const tokens = query.match(/--?[\p{L}\p{N}][\p{L}\p{N}_.:/-]*|[\p{L}\p{N}][\p{L}\p{N}_-]*/gu) ?? [];
	for (const token of tokens) {
		const special = /[_./:-]/.test(token);
		if (special) add(token, true);
		const splitCamel = token.replace(/([\p{Ll}\d])([\p{Lu}])/gu, "$1 $2");
		for (const part of splitCamel.split(/[_./:-]+|\s+/)) add(part);
	}

	return terms.slice(0, MAX_TERMS);
}

function escapeRegex(text: string): string {
	return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeCandidatePath(raw: string, root: string): string | undefined {
	let candidate = raw.replace(/\0/g, "").trim();
	if (!candidate) return undefined;
	if (candidate.startsWith("./")) candidate = candidate.slice(2);
	if (isAbsolute(candidate)) candidate = relative(root, candidate);
	candidate = candidate.replaceAll("\\", "/");
	if (!candidate || candidate === "." || candidate === ".." || candidate.startsWith("../")) return undefined;
	return candidate;
}

/** Merge `rg --json` content hits and NUL-delimited `fd` filename hits. */
export function collectCandidates(
	rgOutput: string,
	fdOutput: string,
	terms: string[],
	root = process.cwd(),
): SearchCandidate[] {
	type Accumulator = SearchCandidate & { matchedTerms: Set<string>; pathTerms: Set<string> };
	const byPath = new Map<string, Accumulator>();

	const get = (path: string): Accumulator => {
		let candidate = byPath.get(path);
		if (!candidate) {
			candidate = {
				path,
				matches: [],
				lexicalScore: 0,
				pathMatch: false,
				matchedTerms: new Set(),
				pathTerms: new Set(),
			};
			byPath.set(path, candidate);
		}
		return candidate;
	};

	for (const row of rgOutput.split("\n")) {
		if (!row.trim()) continue;
		let event: any;
		try {
			event = JSON.parse(row);
		} catch {
			continue; // A command-output cap can leave one partial JSON record at the end.
		}
		if (event?.type !== "match") continue;
		const path = normalizeCandidatePath(event?.data?.path?.text ?? "", root);
		const line = Number(event?.data?.line_number);
		const text = String(event?.data?.lines?.text ?? "").replace(/\r?\n$/, "").slice(0, MAX_LINE_CHARS);
		if (!path || !Number.isInteger(line) || line < 1) continue;

		const candidate = get(path);
		if (!candidate.matches.some((match) => match.line === line)) {
			candidate.matches.push({ line, text });
		}
		const lower = text.toLowerCase();
		for (const term of terms) {
			if (lower.includes(term)) candidate.matchedTerms.add(term);
		}
	}

	for (const raw of fdOutput.split("\0")) {
		const path = normalizeCandidatePath(raw, root);
		if (!path) continue;
		const candidate = get(path);
		candidate.pathMatch = true;
		const lower = path.toLowerCase();
		for (const term of terms) {
			if (lower.includes(term)) candidate.pathTerms.add(term);
		}
	}

	return [...byPath.values()]
		.map((candidate) => ({
			path: candidate.path,
			matches: candidate.matches.sort((a, b) => a.line - b.line).slice(0, 8),
			lexicalScore:
				candidate.matchedTerms.size * 20 +
				Math.min(candidate.matches.length, 8) * 2 +
				candidate.pathTerms.size * 8 +
				(candidate.pathMatch ? 4 : 0),
			pathMatch: candidate.pathMatch,
		}))
		.sort((a, b) => b.lexicalScore - a.lexicalScore || a.path.localeCompare(b.path));
}

/** Run a discovery command without a shell; query text can never become code. */
export function runDiscoveryCommand(
	command: string,
	args: string[],
	cwd: string,
	signal?: AbortSignal,
): Promise<CommandResult> {
	return new Promise((resolvePromise, rejectPromise) => {
		if (signal?.aborted) {
			rejectPromise(abortError());
			return;
		}

		const child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
		const stdout: Buffer[] = [];
		const stderr: Buffer[] = [];
		let stdoutBytes = 0;
		let stderrBytes = 0;
		let truncated = false;
		let timedOut = false;
		let aborted = false;
		let settled = false;

		const finishReject = (error: Error) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			rejectPromise(error);
		};

		const stop = () => {
			if (!child.killed) child.kill("SIGTERM");
		};
		const onAbort = () => {
			aborted = true;
			stop();
		};
		const timer = setTimeout(() => {
			timedOut = true;
			stop();
		}, DISCOVERY_TIMEOUT_MS);
		signal?.addEventListener("abort", onAbort, { once: true });

		child.stdout.on("data", (chunk: Buffer) => {
			const remaining = MAX_COMMAND_OUTPUT_BYTES - stdoutBytes;
			if (remaining > 0) {
				const kept = chunk.subarray(0, remaining);
				stdout.push(kept);
				stdoutBytes += kept.length;
			}
			if (chunk.length > remaining) {
				truncated = true;
				stop();
			}
		});
		child.stderr.on("data", (chunk: Buffer) => {
			const remaining = 8_000 - stderrBytes;
			if (remaining <= 0) return;
			const kept = chunk.subarray(0, remaining);
			stderr.push(kept);
			stderrBytes += kept.length;
		});
		child.on("error", (error) => finishReject(error));
		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);

			if (aborted) {
				rejectPromise(abortError());
				return;
			}
			const result = {
				stdout: Buffer.concat(stdout).toString("utf8"),
				stderr: Buffer.concat(stderr).toString("utf8"),
				truncated: truncated || timedOut,
			};
			// rg exits 1 for a valid search with no matches. Partial output is still
			// useful when a very large tree reaches the time/size guard.
			const executable = command.replaceAll("\\", "/").split("/").at(-1);
			const rgNoMatches = executable === "rg" && code === 1;
			if (code === 0 || rgNoMatches || truncated || (timedOut && stdoutBytes > 0)) {
				resolvePromise(result);
				return;
			}
			const reason = timedOut
				? `${command} did not finish within ${DISCOVERY_TIMEOUT_MS / 1000}s`
				: `${command} exited ${code}: ${result.stderr.trim() || "no error output"}`;
			rejectPromise(new Error(reason));
		});
	});
}

async function discoverCandidates(
	root: string,
	terms: string[],
	signal: AbortSignal | undefined,
	run: CommandRunner,
): Promise<{ candidates: SearchCandidate[]; warnings: string[] }> {
	const rgArgs = [
		"--json", "--ignore-case", "--fixed-strings", "--hidden", "--no-messages",
		"--max-count", "8", "--max-filesize", "1M",
	];
	for (const dir of EXCLUDED_DIRS) rgArgs.push("--glob", `!${dir}/**`);
	for (const term of terms) rgArgs.push("-e", term);
	rgArgs.push(".");

	const fdPattern = terms.map(escapeRegex).join("|");
	const fdArgs = ["--type", "f", "--hidden", "--ignore-case", "--color", "never", "--print0"];
	for (const dir of EXCLUDED_DIRS) fdArgs.push("--exclude", dir);
	fdArgs.push("--", fdPattern, ".");

	const [rgResult, fdResult] = await Promise.allSettled([
		run(process.env.LOCAL_AI_RG_BIN ?? "rg", rgArgs, root, signal),
		run(process.env.LOCAL_AI_FD_BIN ?? "fd", fdArgs, root, signal),
	]);
	const warnings: string[] = [];
	if (rgResult.status === "rejected") warnings.push(`rg: ${errorMessage(rgResult.reason)}`);
	if (fdResult.status === "rejected") warnings.push(`fd: ${errorMessage(fdResult.reason)}`);
	if (rgResult.status === "rejected" && fdResult.status === "rejected") {
		if (rgResult.reason?.name === "AbortError" || fdResult.reason?.name === "AbortError") throw abortError();
		throw new Error(`Candidate discovery failed (${warnings.join("; ")})`);
	}
	if (rgResult.status === "fulfilled" && rgResult.value.truncated) warnings.push("rg: candidate output was capped");
	if (fdResult.status === "fulfilled" && fdResult.value.truncated) warnings.push("fd: candidate output was capped");

	return {
		candidates: collectCandidates(
			rgResult.status === "fulfilled" ? rgResult.value.stdout : "",
			fdResult.status === "fulfilled" ? fdResult.value.stdout : "",
			terms,
			root,
		).slice(0, MAX_CANDIDATES),
		warnings,
	};
}

async function hydrateCandidate(root: string, candidate: SearchCandidate): Promise<HydratedCandidate> {
	const absolute = resolve(root, candidate.path);
	if (absolute !== root && !absolute.startsWith(`${root}${sep}`)) {
		throw new Error(`Candidate escaped the search root: ${candidate.path}`);
	}

	let text = "";
	try {
		const info = await stat(absolute);
		if (info.isFile() && info.size <= MAX_FILE_BYTES) text = await readFile(absolute, "utf8");
	} catch {
		// A file can disappear between rg/fd and the read. Its matched line still
		// gives the reranker something useful, so do not discard the candidate.
	}
	if (text.includes("\0")) text = ""; // Binary filename matches are ranked by path only.

	const rendered = new Map<number, string>();
	if (text) {
		const lines = text.split(/\r?\n/);
		if (candidate.matches.length > 0) {
			for (const match of candidate.matches) {
				for (let index = Math.max(0, match.line - 2); index <= Math.min(lines.length - 1, match.line); index++) {
					rendered.set(index + 1, lines[index].slice(0, MAX_LINE_CHARS));
				}
			}
		} else {
			for (let index = 0; index < Math.min(lines.length, 40); index++) {
				if (lines[index].trim()) rendered.set(index + 1, lines[index].slice(0, MAX_LINE_CHARS));
			}
		}
	}
	if (rendered.size === 0) {
		for (const match of candidate.matches) rendered.set(match.line, match.text);
	}

	let excerpt = [...rendered.entries()]
		.sort((a, b) => a[0] - b[0])
		.map(([line, value]) => `${line}: ${value}`)
		.join("\n")
		.slice(0, MAX_DOCUMENT_CHARS);
	if (!excerpt) excerpt = "(filename match; no readable text excerpt)";

	return {
		...candidate,
		document: `File: ${candidate.path}\n${excerpt}`,
		excerpt,
		locationLine: candidate.matches[0]?.line,
	};
}

/** Validate and order the response, including the known broken-GGUF sentinel. */
export function rankRerankResults(payload: unknown, candidates: HydratedCandidate[]): RankedCandidate[] {
	const results = (payload as any)?.results;
	if (!Array.isArray(results) || results.length === 0) {
		throw new Error("The reranker response did not contain a non-empty results array.");
	}

	const seen = new Set<number>();
	const ranked: RankedCandidate[] = [];
	for (const result of results) {
		const index = result?.index;
		const score = result?.relevance_score;
		if (!Number.isInteger(index) || index < 0 || index >= candidates.length || !Number.isFinite(score)) {
			throw new Error("The reranker returned an invalid candidate index or relevance_score.");
		}
		if (seen.has(index)) throw new Error(`The reranker returned candidate index ${index} more than once.`);
		seen.add(index);
		ranked.push({ ...candidates[index], relevanceScore: score });
	}
	if (seen.size !== candidates.length) {
		throw new Error(`The reranker scored ${seen.size} of ${candidates.length} candidates.`);
	}

	const maxScore = Math.max(...ranked.map((candidate) => candidate.relevanceScore));
	if (maxScore <= MIN_HEALTHY_SCORE) {
		throw new Error(
			`Degenerate reranker scores (max=${maxScore}). The configured GGUF is probably missing ` +
			"cls.output.weight; replace the model rather than trusting this ranking.",
		);
	}
	return ranked.sort((a, b) => b.relevanceScore - a.relevanceScore || a.path.localeCompare(b.path));
}

async function requestRerank(
	query: string,
	candidates: HydratedCandidate[],
	signal: AbortSignal | undefined,
	fetchImpl: typeof fetch,
): Promise<{ ranked: RankedCandidate[]; documentLimit: number }> {
	const baseUrl = (process.env.LOCAL_AI_BASE_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
	const model = process.env.LOCAL_AI_RERANK_MODEL ?? DEFAULT_MODEL;
	const controller = new AbortController();
	const onAbort = () => controller.abort();
	if (signal?.aborted) controller.abort();
	else signal?.addEventListener("abort", onAbort, { once: true });
	const timer = setTimeout(() => controller.abort(), RERANK_TIMEOUT_MS);

	try {
		for (const [attempt, documentLimit] of RERANK_DOCUMENT_LIMITS.entries()) {
			const response = await fetchImpl(`${baseUrl}/rerank`, {
				method: "POST",
				headers: { "Content-Type": "application/json", Authorization: "Bearer local" },
				signal: controller.signal,
				body: JSON.stringify({
					model,
					query,
					documents: candidates.map((candidate) => candidate.document.slice(0, documentLimit)),
				}),
			});
			if (response.ok) {
				return { ranked: rankRerankResults(await response.json(), candidates), documentLimit };
			}

			const body = await response.text().catch(() => "");
			const physicalBatchTooSmall =
				response.status === 500 && /too large to process|physical batch|batch size limit/i.test(body);
			if (physicalBatchTooSmall && attempt < RERANK_DOCUMENT_LIMITS.length - 1) continue;
			throw new Error(
				`The reranker returned HTTP ${response.status}. Check that llama-swap is running and ` +
				`"${model}" is in its config. ${body.slice(0, 500)}`.trim(),
			);
		}
		throw new Error("The reranker exhausted its document-size retries.");
	} catch (error) {
		if ((error as Error)?.name === "AbortError") {
			if (signal?.aborted) throw abortError();
			throw new Error(`The reranker did not answer within ${RERANK_TIMEOUT_MS / 1000}s.`);
		}
		throw error;
	} finally {
		clearTimeout(timer);
		signal?.removeEventListener("abort", onAbort);
	}
}

export function defaultQueryLogPath(): string {
	const configured = process.env.LOCAL_AI_PROJECT_SEARCH_LOG;
	if (configured) return resolve(configured);

	const piAgentDir = process.env.PI_CODING_AGENT_DIR
		? resolve(process.env.PI_CODING_AGENT_DIR)
		: resolve(homedir(), ".pi", "agent");
	return resolve(piAgentDir, "project-search", "queries.jsonl");
}

async function writeQueryLog(path: string, record: QueryLogRecord): Promise<string | undefined> {
	try {
		await mkdir(dirname(path), { recursive: true, mode: 0o700 });
		await appendFile(path, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
		return undefined;
	} catch (error) {
		return `Could not append the query log at ${path}: ${errorMessage(error)}`;
	}
}

function renderResults(results: RankedCandidate[], candidateCount: number, root: string): string {
	const out = [`project_search: ${results.length} result(s) from ${candidateCount} candidates under ${root}`];
	for (const [index, result] of results.entries()) {
		const location = result.locationLine ? `${result.path}:${result.locationLine}` : result.path;
		out.push("", `${index + 1}. ${location}  (score ${result.relevanceScore.toPrecision(4)})`);
		for (const line of result.excerpt.split("\n").slice(0, 6)) out.push(`   ${line}`);
	}
	return out.join("\n");
}

function withWarning(message: string, warning?: string): string {
	return warning ? `${message}\n\nWarning: ${warning}` : message;
}

/** Full tool body, exported so the no-toolchain `.mjs` harness can exercise it. */
export async function executeProjectSearch(
	params: { query: string; path?: string },
	cwd: string,
	signal?: AbortSignal,
	dependencies: SearchDependencies = {},
) {
	const query = String(params.query ?? "").trim();
	const terms = queryTerms(query);
	const logPath = dependencies.logPath ?? defaultQueryLogPath();
	const now = dependencies.now ?? (() => new Date());
	let root = resolve(cwd, params.path || ".");
	let candidates: HydratedCandidate[] = [];
	let warnings: string[] = [];

	const log = async (
		status: QueryLogRecord["status"],
		results: RankedCandidate[] = [],
		error?: string,
	) => writeQueryLog(logPath, {
		schema: 1,
		timestamp: now().toISOString(),
		status,
		query,
		root,
		terms,
		candidates: candidates.map((candidate) => candidate.path),
		results: results.map((result) => ({ path: result.path, score: result.relevanceScore })),
		error: error?.slice(0, 1_000),
	});

	if (!query) return errorResult("project_search needs a non-empty query.", { queryLog: logPath });
	if (terms.length === 0) {
		const message = "The query contains no usable literal terms for rg/fd candidate discovery.";
		const logWarning = await log("error", [], message);
		return errorResult(withWarning(message, logWarning), { query, root, terms, queryLog: logPath, logWarning });
	}

	try {
		root = await realpath(root);
		const info = await stat(root);
		if (!info.isDirectory()) throw new Error(`Not a directory: ${root}`);

		const discovered = await discoverCandidates(
			root,
			terms,
			signal,
			dependencies.runCommand ?? runDiscoveryCommand,
		);
		warnings = discovered.warnings;
		candidates = await Promise.all(discovered.candidates.map((candidate) => hydrateCandidate(root, candidate)));

		if (candidates.length === 0) {
			const logWarning = await log("no_candidates");
			const message =
				`No candidate files matched ${terms.map((term) => JSON.stringify(term)).join(", ")} under ${root}. ` +
				"Try a distinctive symbol, filename fragment, or domain term.";
			return {
				content: [{ type: "text" as const, text: withWarning(message, logWarning) }],
				details: { query, root, terms, candidates: 0, warnings, queryLog: logPath, logWarning },
			};
		}

		const rerank = await requestRerank(
			query,
			candidates,
			signal,
			dependencies.fetch ?? fetch,
		);
		if (rerank.documentLimit < MAX_DOCUMENT_CHARS) {
			warnings.push(`reranker excerpts reduced to ${rerank.documentLimit} characters for its physical batch limit`);
		}
		const top = rerank.ranked.slice(0, MAX_RESULTS);
		const logWarning = await log("ok", top);
		const warningText = [...warnings, ...(logWarning ? [logWarning] : [])].join("; ") || undefined;
		return {
			content: [{ type: "text" as const, text: withWarning(renderResults(top, candidates.length, root), warningText) }],
			details: {
				query,
				root,
				terms,
				model: process.env.LOCAL_AI_RERANK_MODEL ?? DEFAULT_MODEL,
				candidateCount: candidates.length,
				results: top.map((result) => ({
					path: result.path,
					line: result.locationLine,
					relevanceScore: result.relevanceScore,
				})),
				warnings,
				queryLog: logPath,
				logWarning,
			},
		};
	} catch (error) {
		const cancelled = (error as Error)?.name === "AbortError";
		const message = cancelled ? "project_search was cancelled." : errorMessage(error);
		const logWarning = await log(cancelled ? "cancelled" : "error", [], message);
		return errorResult(withWarning(message, logWarning), {
			query,
			root,
			terms,
			candidateCount: candidates.length,
			warnings,
			queryLog: logPath,
			logWarning,
		});
	}
}

export default function projectSearchExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "project_search",
		label: "Project Search",
		description:
			"Find the project files most relevant to a natural-language query. Uses rg/fd for " +
			"candidate recall and a local reranker for semantic ordering; returns the top five files with excerpts.",
		promptSnippet: "Find project files by concept using local semantic reranking",
		promptGuidelines: [
			"Use project_search when you know the behavior or concept you need but not the exact filename or symbol.",
			"Use grep for a known exact string; project_search is for discovery, not proof that a string is absent.",
			"Treat project_search results as candidate files and read the relevant files before editing them.",
		],
		parameters: PARAMS,
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			return executeProjectSearch(params, ctx.cwd, signal);
		},
	});
}

function abortError(): Error {
	const error = new Error("Operation cancelled");
	error.name = "AbortError";
	return error;
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

function errorResult(message: string, details: Record<string, unknown>) {
	return {
		content: [{ type: "text" as const, text: message }],
		details,
		isError: true,
	};
}
