/**
 * Unit and boundary tests for project-search.ts.
 *
 * Run directly, or through tests/run-all.sh (offline check 18):
 *
 *     node --test tests/project-search.test.mjs
 *
 * Like the other extension harness, this is plain JavaScript with no package
 * or build step. The deployed extension must remain one self-contained `.ts`
 * file, so the harness copies it to a temporary directory, removes the type-
 * only Pi import, and supplies the tiny TypeBox surface used at module load.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
	chmodSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	realpathSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "pi-extensions", "project-search.ts"), "utf8");
const stripped = source
	.replace(/^import type .*@earendil-works\/pi-coding-agent";$/gm, "")
	.replace(
		/^import \{ Type \} from "typebox";$/m,
		"const Type = { Object: (v) => v, String: (v) => v, Optional: (v) => v };",
	);
const moduleScratch = mkdtempSync(join(tmpdir(), "project-search-module-"));
const modulePath = join(moduleScratch, "project-search.ts");
writeFileSync(modulePath, stripped, "utf8");

const m = await import(pathToFileURL(modulePath).href);

function rgMatch(path, line, text) {
	return JSON.stringify({
		type: "match",
		data: { path: { text: `./${path}` }, lines: { text: `${text}\n` }, line_number: line },
	});
}

function hydrated(path) {
	return {
		path,
		matches: [],
		lexicalScore: 1,
		pathMatch: false,
		document: `File: ${path}`,
		excerpt: "1: example",
	};
}

test("query terms preserve code identifiers while dropping question filler", () => {
	const terms = m.queryTerms("Where is project_search handling --pin-system-prompt?");
	assert.ok(terms.includes("project_search"));
	assert.ok(terms.includes("project"));
	assert.ok(terms.includes("--pin-system-prompt"));
	assert.ok(!terms.includes("where"));
	assert.ok(!terms.includes("is"));
});

test("rg content hits and fd filename hits merge into file candidates", () => {
	const rg = [
		rgMatch("scripts/log-rotation.sh", 12, "configure log rotation owner"),
		rgMatch("scripts/log-rotation.sh", 18, "write the rotation file"),
		rgMatch("docs/notes.md", 4, "rotation background"),
		"{partial-json",
	].join("\n");
	const candidates = m.collectCandidates(
		rg,
		"./scripts/log-rotation.sh\0./log-reader.ts\0../outside.txt\0",
		["log", "rotation", "owner"],
		"/tmp/project",
	);

	assert.equal(candidates[0].path, "scripts/log-rotation.sh");
	assert.equal(candidates[0].matches.length, 2);
	assert.equal(candidates[0].pathMatch, true);
	assert.ok(candidates.some((candidate) => candidate.path === "log-reader.ts"));
	assert.ok(!candidates.some((candidate) => candidate.path === "../outside.txt"));
});

test("reranker output is ordered by score, not response order", () => {
	const ranked = m.rankRerankResults(
		{ results: [
			{ index: 0, relevance_score: 0.02 },
			{ index: 1, relevance_score: 0.91 },
		] },
		[hydrated("second.md"), hydrated("first.ts")],
	);
	assert.deepEqual(ranked.map((candidate) => candidate.path), ["first.ts", "second.md"]);
});

test("degenerate scores fail closed instead of returning a plausible ranking", () => {
	assert.throws(
		() => m.rankRerankResults(
			{ results: [
				{ index: 0, relevance_score: 1e-20 },
				{ index: 1, relevance_score: 2e-20 },
			] },
			[hydrated("a.ts"), hydrated("b.ts")],
		),
		/cls\.output\.weight/,
	);
});

test("missing reranker rows are rejected", () => {
	assert.throws(
		() => m.rankRerankResults(
			{ results: [{ index: 0, relevance_score: 0.9 }] },
			[hydrated("a.ts"), hydrated("b.ts")],
		),
		/scored 1 of 2 candidates/,
	);
});

test("the registered tool exposes the planned project_search contract", () => {
	let definition;
	m.default({ registerTool(value) { definition = value; } });
	assert.equal(definition.name, "project_search");
	assert.match(definition.description, /top five files/);
	assert.ok(definition.promptGuidelines.every((line) => line.includes("project_search") || line.startsWith("Use grep")));
});

test("the default query log follows PI_CODING_AGENT_DIR", () => {
	const previousAgentDir = process.env.PI_CODING_AGENT_DIR;
	const previousLogPath = process.env.LOCAL_AI_PROJECT_SEARCH_LOG;
	const agentDir = join(tmpdir(), "custom-pi-agent");
	try {
		process.env.PI_CODING_AGENT_DIR = agentDir;
		delete process.env.LOCAL_AI_PROJECT_SEARCH_LOG;
		assert.equal(m.defaultQueryLogPath(), join(agentDir, "project-search", "queries.jsonl"));
	} finally {
		if (previousAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
		else process.env.PI_CODING_AGENT_DIR = previousAgentDir;
		if (previousLogPath === undefined) delete process.env.LOCAL_AI_PROJECT_SEARCH_LOG;
		else process.env.LOCAL_AI_PROJECT_SEARCH_LOG = previousLogPath;
	}
});

test("the full tool caps results at five and writes a replayable private query log", async () => {
	const project = mkdtempSync(join(tmpdir(), "project-search-fixture-"));
	const files = [
		["scripts/log-rotation.sh", "RIGHT_ANSWER configures newsyslog ownership"],
		["docs/logs.md", "log rotation overview"],
		["src/logger.ts", "logger rotation code"],
		["tests/logs.test.ts", "rotation tests"],
		["README.md", "logs are documented here"],
		["notes/archive.md", `old rotation notes ${"dense_code();".repeat(500)}`],
	];
	for (const [path, text] of files) {
		mkdirSync(dirname(join(project, path)), { recursive: true });
		writeFileSync(join(project, path), `${text}\n`, "utf8");
	}

	const rg = files.map(([path, text]) => rgMatch(path, 1, text)).join("\n");
	const commands = [];
	const runCommand = async (command, args, cwd) => {
		commands.push({ command, args, cwd });
		return {
			stdout: command === "rg" ? rg : "./scripts/log-rotation.sh\0",
			stderr: "",
			truncated: false,
		};
	};

	const requests = [];
	const fetchStub = async (url, options) => {
		const request = { url, options, body: JSON.parse(options.body) };
		requests.push(request);
		if (requests.length === 1) {
			return new Response("input (530 tokens) is too large to process; physical batch size is 512", {
				status: 500,
			});
		}
		const results = request.body.documents.map((document, index) => ({
			index,
			relevance_score: document.includes("RIGHT_ANSWER") ? 0.95 : 0.01 + index / 1_000,
		}));
		return new Response(JSON.stringify({ results }), {
			status: 200,
			headers: { "Content-Type": "application/json" },
		});
	};

	const logPath = join(project, ".test-state", "queries.jsonl");
	const result = await m.executeProjectSearch(
		{ query: "How is log rotation ownership configured?" },
		project,
		undefined,
		{
			runCommand,
			fetch: fetchStub,
			logPath,
			now: () => new Date("2026-08-05T12:00:00.000Z"),
		},
	);

	assert.equal(result.isError, undefined);
	assert.equal(result.details.candidateCount, 6);
	assert.equal(result.details.results.length, 5);
	assert.equal(result.details.results[0].path, "scripts/log-rotation.sh");
	assert.match(result.content[0].text, /scripts\/log-rotation\.sh:1/);
	assert.equal(commands.length, 2);
	assert.ok(commands.every((command) => command.cwd === realpathSync(project)));
	assert.equal(requests.length, 2);
	assert.equal(requests[0].url, "http://localhost:8080/v1/rerank");
	assert.equal(requests[0].body.model, "rerank-qwen3-0.6b");
	assert.equal(requests[0].body.documents.length, 6);
	assert.ok(requests[0].body.documents.every((document) => document.length <= 800));
	assert.ok(requests[1].body.documents.every((document) => document.length <= 400));
	assert.match(result.content[0].text, /excerpts reduced to 400 characters/);

	const records = readFileSync(logPath, "utf8").trim().split("\n").map(JSON.parse);
	assert.equal(records.length, 1);
	assert.equal(records[0].status, "ok");
	assert.equal(records[0].timestamp, "2026-08-05T12:00:00.000Z");
	assert.equal(records[0].candidates.length, 6);
	assert.equal(records[0].results.length, 5);
	assert.equal(statSync(logPath).mode & 0o777, 0o600);
});

test("no lexical candidates is a successful empty search and is still logged", async () => {
	const project = mkdtempSync(join(tmpdir(), "project-search-empty-"));
	const logPath = join(project, "state", "queries.jsonl");
	const result = await m.executeProjectSearch(
		{ query: "unfindable widget protocol" },
		project,
		undefined,
		{
			runCommand: async () => ({ stdout: "", stderr: "", truncated: false }),
			fetch: async () => { throw new Error("fetch must not run"); },
			logPath,
		},
	);

	assert.equal(result.isError, undefined);
	assert.match(result.content[0].text, /No candidate files matched/);
	assert.equal(JSON.parse(readFileSync(logPath, "utf8")).status, "no_candidates");
});

test("the command runner preserves argv boundaries", async () => {
	const scratch = mkdtempSync(join(tmpdir(), "project-search-argv-"));
	const probe = join(scratch, "argv-probe");
	writeFileSync(probe, "#!/bin/sh\nprintf '%s\\0' \"$@\"\n", "utf8");
	chmodSync(probe, 0o700);

	const result = await m.runDiscoveryCommand(
		probe,
		["literal; touch should-not-exist", "$(touch also-should-not-exist)", "space kept"],
		scratch,
	);
	assert.deepEqual(result.stdout.split("\0").filter(Boolean), [
		"literal; touch should-not-exist",
		"$(touch also-should-not-exist)",
		"space kept",
	]);
	assert.throws(() => statSync(join(scratch, "should-not-exist")));
	assert.throws(() => statSync(join(scratch, "also-should-not-exist")));
});
