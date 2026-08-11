/**
 * Unit tests for compact-test-output.ts's deterministic parts.
 *
 * Run directly, or through tests/run-all.sh (offline check 17):
 *
 *     node --test tests/compact-test-output.test.mjs
 *
 * No toolchain, no package.json, no node_modules, and no TypeScript: this file
 * is plain JS. `.mjs` rather than `.js` because without a package.json a `.js`
 * file is CommonJS unless node guesses from the syntax — `.mjs` says ES module
 * outright and has always meant that.
 *
 * The module under test stays TypeScript, and that is not a preference: Pi
 * discovers `~/.pi/agent/extensions/*.ts`, and steps/45-extensions.sh installs
 * by that glob. Node 24 strips the types on import, so nothing here needs a
 * build step either way.
 *
 * The extension is deployed as a *single self-contained file* copied flat into
 * that directory, so the parsers cannot be split into a module of their own
 * just to make them importable. Instead the source is copied to a temp file
 * with its one external import removed — the `@earendil-works/pi-coding-agent`
 * package is not resolvable from this repo, and `isBashToolResult` is only
 * referenced inside the event handler, which these tests never invoke.
 * Everything else is exercised as written.
 *
 * Every case below is a bug these parsers actually had. They had never been
 * fired in production when the tests were written (PLAN §1.4).
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "pi-extensions", "compact-test-output.ts"), "utf8");
const stripped = src.replace(/^import .*@earendil-works\/pi-coding-agent";$/gm, "");
const scratch = mkdtempSync(join(tmpdir(), "compact-test-"));
const modPath = join(scratch, "compact-test-output.ts");
writeFileSync(modPath, stripped, "utf8");

const m = await import(pathToFileURL(modPath).href);

// ── runner detection ────────────────────────────────────────────────────────
// The pattern used to be unanchored and matched against the whole line, so any
// command that merely mentioned a runner was treated as a test run and had its
// output summarized away.

test("a runner as the program is detected", () => {
	for (const cmd of [
		"pytest -q",
		"npm test",
		"npm run lint",
		"cargo test --all",
		"go test ./...",
		"./node_modules/.bin/jest --ci",
		"/usr/local/bin/pytest tests/",
		"make check",
	]) {
		assert.equal(m.looksLikeRunner(cmd), true, cmd);
	}
});

test("a runner named only as an argument is NOT a test run", () => {
	for (const cmd of [
		"cat pytest-output.log",
		"grep -r jest .",
		"ls -la eslint.config.js",
		"rm -f jest.config.js",
		"echo 'run pytest later'",
		"git log --grep=rubocop",
	]) {
		assert.equal(m.looksLikeRunner(cmd), false, cmd);
	}
});

test("runners are found after pipes, lists and wrappers", () => {
	for (const cmd of [
		"cd /app && pytest -q",
		"echo start; jest --ci",
		"cat foo | rubocop --stdin foo.rb",
		"CI=1 pytest -q",
		"CI=1 FORCE_COLOR=0 npm test",
		"time pytest -q",
		"timeout 300 jest --ci",
		"nice -n 10 cargo test",
		"env pytest -q",
	]) {
		assert.equal(m.looksLikeRunner(cmd), true, cmd);
	}
});

// ── rtk detection ───────────────────────────────────────────────────────────
// Missing an rtk prefix means compacting output a second time, after rtk has
// already shrunk it.

test("rtk is detected wherever it is the program", () => {
	for (const cmd of [
		"rtk pytest",
		"rtk git diff",
		"cd /app && rtk npm test",
		"echo x | rtk cat",
		"/opt/homebrew/bin/rtk pytest",
		"CI=1 rtk pytest",
		"time rtk pytest",
		"FOO=bar BAZ=1 rtk npm test",
	]) {
		assert.equal(m.usesRtk(cmd), true, cmd);
	}
});

test("rtk mentioned as an argument is not rtk usage", () => {
	for (const cmd of ["cat rtk.log", "echo rtk", "which rtk", "grep rtk Makefile"]) {
		assert.equal(m.usesRtk(cmd), false, cmd);
	}
});

// ── junit / pytest XML ──────────────────────────────────────────────────────

test("pytest's bare <testsuites> wrapper does not report a failing run as passed", () => {
	// pytest puts the counts on the inner <testsuite>, leaving the wrapper bare.
	// Reading only the wrapper gave 0 failures, and 0 failures rendered "passed".
	const xml = `<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="2" skipped="1" tests="7" time="1.2">
    <testcase classname="tests.test_api" name="test_ok" time="0.01"/>
    <testcase classname="tests.test_api" name="test_create" time="0.02">
      <failure message="AssertionError: expected 201, got 500">Traceback...</failure>
    </testcase>
    <testcase classname="tests.test_api" name="test_delete" time="0.02">
      <failure message="KeyError: &apos;id&apos;">Traceback...</failure>
    </testcase>
  </testsuite>
</testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.status, "failed");
	assert.equal(s.category, "junit-xml");
	assert.match(s.note, /7 test\(s\), 2 failure\(s\)/);
	assert.equal(s.findings.length, 2);
	assert.equal(s.findings[0].name, "tests.test_api.test_create");
	assert.match(s.findings[0].message, /expected 201, got 500/);
	assert.equal(s.findings[1].message, "KeyError: 'id'"); // XML entity decoded
});

test("counts on the <testsuites> header are still used when present", () => {
	const xml = `<testsuites tests="10" failures="1" errors="0">
  <testsuite name="a" tests="10" failures="1">
    <testcase classname="A" name="t1"><failure message="boom">x</failure></testcase>
  </testsuite>
</testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.status, "failed");
	// The header is authoritative; suite counts must not be added on top of it.
	assert.match(s.note, /^10 test\(s\), 1 failure\(s\)/);
});

test("a genuinely passing report still reads as passed", () => {
	const xml = `<testsuites>
  <testsuite name="pytest" errors="0" failures="0" tests="3">
    <testcase classname="A" name="t1"/><testcase classname="A" name="t2"/>
  </testsuite>
</testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.status, "passed");
	assert.equal(s.findings.length, 0);
});

test("parsed failures outrank absent counts", () => {
	// No counts anywhere, but a <failure> element is present. Reporting "passed"
	// above a list of failures is the one outcome that actively misleads.
	const xml = `<testsuites><testsuite name="x">
    <testcase classname="A" name="t1"><failure message="nope">x</failure></testcase>
  </testsuite></testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.status, "failed");
	assert.equal(s.findings.length, 1);
});

test("a self-closing <testcase/> does not inherit the next test's failure", () => {
	// Passing tests are emitted self-closing. Treating that as an opening tag
	// made the body match run on to the *next* </testcase>, so the passing test
	// was reported as the one that failed — pointing at innocent code.
	const xml = `<testsuites><testsuite name="s" tests="2" failures="1">
    <testcase classname="A" name="passing_one"/>
    <testcase classname="A" name="failing_one"><failure message="boom">t</failure></testcase>
  </testsuite></testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.findings.length, 1);
	assert.equal(s.findings[0].name, "A.failing_one");
	assert.doesNotMatch(JSON.stringify(s.findings), /passing_one/);
});

test("classname and name are read as separate attributes", () => {
	// `/name="/` matches inside `classname="`, so without a word boundary every
	// finding was named after its class twice.
	const xml = `<testsuites><testsuite tests="1" failures="1">
    <testcase classname="pkg.mod.Case" name="test_thing" file="tests/t.py">
      <failure message="nope">tb</failure></testcase>
  </testsuite></testsuites>`;
	const s = m.parseJunitXml(xml);
	assert.equal(s.findings[0].name, "pkg.mod.Case.test_thing");
	assert.equal(s.findings[0].location, "tests/t.py");
});

test("non-junit text is declined rather than guessed at", () => {
	assert.equal(m.parseJunitXml("just some log output"), undefined);
	assert.equal(m.parseJunitXml("<html><body>hi</body></html>"), undefined);
});

// ── JSON parsers ────────────────────────────────────────────────────────────

test("eslint JSON separates errors from warnings", () => {
	const doc = [
		{
			filePath: "/app/src/a.ts",
			messages: [
				{ severity: 2, ruleId: "no-unused-vars", message: "x is unused", line: 3, column: 7 },
				{ severity: 1, ruleId: "prefer-const", message: "use const", line: 9, column: 1 },
			],
		},
	];
	const s = m.parseEslint(doc);
	assert.equal(s.status, "failed");
	assert.match(s.note, /1 error\(s\), 1 warning\(s\)/);
	assert.equal(s.findings.length, 1); // warnings are counted, not listed
	assert.equal(s.findings[0].location, "/app/src/a.ts:3:7");
});

test("eslint JSON with only warnings is not a failure", () => {
	const doc = [{ filePath: "a.ts", messages: [{ severity: 1, ruleId: "r", message: "m", line: 1, column: 1 }] }];
	assert.equal(m.parseEslint(doc).status, "passed with warnings");
});

test("jest JSON reports the failed count", () => {
	const doc = {
		numTotalTests: 12,
		numPassedTests: 10,
		numFailedTests: 2,
		testResults: [
			{
				name: "/app/a.test.ts",
				assertionResults: [
					{ status: "passed", fullName: "adds" },
					{ status: "failed", fullName: "subtracts", failureMessages: ["Expected 1\n  at /app/a.test.ts:9:3"] },
				],
			},
		],
	};
	const s = m.parseJest(doc);
	assert.equal(s.status, "failed");
	assert.match(s.note, /10\/12 passed, 2 failed/);
	assert.equal(s.findings[0].name, "subtracts");
});

test("each parser declines input shaped for another", () => {
	const eslintDoc = [{ filePath: "a.ts", messages: [] }];
	assert.equal(m.parseJest(eslintDoc), undefined);
	assert.equal(m.parseRubocop(eslintDoc), undefined);
	assert.equal(m.parseRspec(eslintDoc), undefined);
	assert.equal(m.parseEslint({ numTotalTests: 1, testResults: [] }), undefined);
	assert.equal(m.parseEslint([]), undefined);
});

test("tryDeterministic routes each format to its own parser", () => {
	assert.equal(m.tryDeterministic(JSON.stringify([{ filePath: "a.ts", messages: [] }])).category, "eslint");
	assert.equal(
		m.tryDeterministic(JSON.stringify({ numTotalTests: 1, numPassedTests: 1, numFailedTests: 0, testResults: [] }))
			.category,
		"jest",
	);
	assert.equal(m.tryDeterministic("<testsuites><testsuite tests=\"1\"/></testsuites>").category, "junit-xml");
	assert.equal(m.tryDeterministic("not structured at all"), undefined);
	assert.equal(m.tryDeterministic("{ truncated json"), undefined);
});

// ── stack frames ────────────────────────────────────────────────────────────

test("the first project frame is preferred over library frames", () => {
	const msg = [
		"NoMethodError: undefined method `foo'",
		"  /usr/local/bundle/gems/rspec-core-3.12.0/lib/rspec.rb:12:in `run'",
		"  /app/lib/thing.rb:42:in `call'",
	].join("\n");
	assert.match(m.firstProjectFrame(msg), /\/app\/lib\/thing\.rb:42/);
});

test("a message with no project frame still returns the message", () => {
	assert.match(m.firstProjectFrame("Something broke"), /Something broke/);
	assert.equal(m.firstProjectFrame(undefined, undefined), undefined);
});

// ── size reporting and truncation ───────────────────────────────────────────
// The line-count gate never fired on one-line JSON, which is exactly what the
// deterministic parsers exist for. Both the gate and the reporting had to learn
// about bytes.

test("size is described in whichever dimension is meaningful", () => {
	assert.equal(m.describeSize(250, 9000), "250 lines");
	assert.equal(m.describeSize(1, 400_000), "400000 bytes");
});

test("truncating one enormous line does not produce a negative omitted count", () => {
	const oneLine = "x".repeat(50_000);
	const out = m.truncateMiddle([oneLine], "/tmp/raw.log");
	assert.doesNotMatch(out, /-\d+ (lines|characters) omitted/);
	assert.match(out, /characters omitted/);
	assert.match(out, /Compacted from 50000 bytes/);
	assert.ok(out.length < oneLine.length, "truncation must actually shrink it");
});

test("truncating many lines keeps both ends", () => {
	const lines = Array.from({ length: 500 }, (_, i) => `line ${i}`);
	const out = m.truncateMiddle(lines, "/tmp/raw.log");
	assert.match(out, /^line 0\n/);
	assert.match(out, /line 499/);
	assert.match(out, /… 400 lines omitted …/);
	assert.match(out, /Compacted from 500 lines/);
});

test("short output is returned whole rather than mangled", () => {
	const out = m.truncateMiddle(["a", "b", "c"], "/tmp/raw.log");
	assert.match(out, /^a\nb\nc\n/);
	assert.doesNotMatch(out, /omitted/);
});
