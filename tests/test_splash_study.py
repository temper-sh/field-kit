"""Matrix routing, first-attempt preservation, streams and frozen submissions."""
import argparse
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from fieldkit_runtime.catalog import Refusal, canonical_json, digest, parse_machine_facts
from fieldkit_runtime.experiments.qwen.coding import CodingStream, performance, request_body
from fieldkit_runtime.experiments.qwen.evaluation import EvaluationFailure, unpack
from fieldkit_runtime.experiments.qwen.patches import InvalidPatch, staged_texts, submission
from fieldkit_runtime.experiments.qwen.splash_contributor import contribute
from fieldkit_runtime.experiments.qwen.splash_study import SplashStudy, bucket, execution_settings, matrix, next_cell
from fieldkit_runtime.experiments.qwen.witness import inspect as review
from fieldkit_runtime.workflow import CommandResult, load_session
from tests.test_catalog import ROOT, facts_bytes
from tests.test_workflow import FakeRunner

PACKAGE = ROOT / "catalog/packages/qwen-machine-study@4"
PROTOCOL = json.loads((PACKAGE / "protocol.json").read_bytes())


def machine(memory=48):
    return facts_bytes(memory*1024**3).replace(b"Apple M fixture", b"Apple M5 Max").replace(b"26.0", b"26.4").replace(b"wired_limit_mib: 24576", b"wired_limit_mib: 36864")


class SyntheticStudy(SplashStudy):
    fail = None
    unsafe = False
    context_fails = False
    requests = None

    def configure(self, cell, directory):
        if cell["id"] == self.fail: raise RuntimeError("Metal out of memory during startup")
        return {"settings": execution_settings(cell, self.limit), "context_execution_sha256": "c"*64,
                "generation": "b"*64, "installation": self.args.installation,
                "lock": str(directory/"execution.lock.json"), "paths": {}, "preparation_seconds": 1.0}

    def probe(self, cell, material, directory):
        return SimpleNamespace(start=lambda: None, finish=lambda: {"safe_to_cleanup": not self.unsafe,
            "samples": 2, "issues": [], "roles": {"engine": {"rss_bytes_max": 10*1024**3}}, "swap_growth_bytes": 0})

    def coding_case(self, probe, cell, material, case, directory, *, memory=None):
        self.requests.append((cell["id"],case["id"]))
        response = {"id":case["id"], "message":{"content":"", "tool_calls":[]}, "failure":None,
            "finish_reason":"length", "status":"context-window-exhausted", "stream_complete":True,
            "service_seconds":10.0, "first_token_seconds":1.0, "first_answer_seconds":None,
            "usage":{"prompt_tokens":100, "completion_tokens":32}, "timings":None,
            "max_tokens":cell["window"]-100, "reasoning_characters":64}
        response.update(performance(response,100))
        return response

    def context_cases(self, probe, cell, material, directory, *, memory=None):
        from fieldkit_runtime.experiments.qwen.method import record_value
        rows=[]
        for identity,keys in (("distributed-ledger",(1,3,5)),("ledger-followup",(2,4))):
            self.requests.append((cell["id"],identity))
            expected={f"R{i:02d}":record_value(4242,i-1) for i in keys}
            count=cell["window"]-5120
            row={"id":identity, "message":{"content":json.dumps({} if self.context_fails else expected),"tool_calls":[]},
                 "expected":expected,"correct":not self.context_fails,"stream_complete":True,"finish_reason":"stop",
                 "service_seconds":10.0,"first_token_seconds":1.0,"first_answer_seconds":2.0,
                 "usage":{"prompt_tokens":count,"completion_tokens":32},"timings":None,"failure":None,"max_tokens":4096}
            row.update(performance(row,count));rows.append(row)
            if self.context_fails: break
        return rows


class MatrixRunner(FakeRunner):
    def execution(self, lock):
        return {"schema": "temper-execution/v1", "profile": "splash-q4", "layouts": ["splash-q4"],
                "lock_sha256": digest(lock.read_bytes()), "execution_digest": "a"*64,
                "request_defaults": {"splash-q4": {}}}

    def __init__(self, **settings):
        super().__init__();self.settings=settings;self.actions=[];self.requests=[]
    def __call__(self, arguments, timeout):
        argv=list(arguments)
        if argv[-1:]==["version"]: return CommandResult(b"temper 0.1.0-alpha.11\n",b"",0)
        if argv[1:3] == ["execution", "configure"]:
            if "--dry-run" not in argv:
                raise AssertionError("the preview must not write a lock")
            # Temper validates output paths even for a dry run. Planning has
            # not created the session or its installation directory yet.
            if not Path(argv[argv.index("--out")+1]).parent.is_dir():
                return CommandResult(b"", b"output parent must already be a real directory", 1)
            settings = {"context_window_tokens": int(argv[argv.index("--context")+1]),
                        "max_output_tokens": int(argv[argv.index("--max-output")+1])}
            if "--max-memory" in argv:
                settings["max_memory_bytes"] = int(argv[argv.index("--max-memory")+1])
            return CommandResult(canonical_json({"schema": "temper-execution-configuration/v1",
                "preset": argv[argv.index("--preset")+1], "settings": settings, "context_execution_sha256": "c"*64}), b"", 0)
        if "--action" in argv:
            args=argparse.Namespace(**{argv[i][2:].replace("-","_"):argv[i+1] for i in range(2,len(argv),2)})
            study=SyntheticStudy(args,Path(argv[1]).parent)
            study.requests=self.requests
            for key,value in self.settings.items(): setattr(study,key,value)
            self.actions.append(study.action["id"])
            with patch("fieldkit_runtime.experiments.qwen.splash_study.monitored_call",return_value={}):
                result=study.run()
            Path(args.report).write_bytes(canonical_json(result))
            return CommandResult(b"",b"",0)
        return super().__call__(argv,timeout)


class SplashStudyTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="qwen matrix ");self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def copy_catalog(self):
        # Revision 4 remains a historical producer; the current index selects 5.
        shutil.copytree(ROOT / "catalog", self.root / "catalog")
        package = self.root / "catalog/packages/qwen-machine-study@4/package.json"
        index = {"schema": "field-kit-question-catalog/v3", "revision": 4,
                 "compiled_at": "2026-10-08T00:00:00Z", "questions": [{
                     "id": "qwen-machine-study", "revision": 4, "availability": "qualifying",
                     "package_path": "packages/qwen-machine-study@4/package.json",
                     "package_sha256": digest(package.read_bytes()), "reason": "Historical matrix under test."}]}
        (self.root / "catalog/questions.json").write_bytes(canonical_json(index))

    def test_configuration_uses_opaque_lock_and_explicit_limits(self):
        study = object.__new__(SplashStudy)
        study.args = SimpleNamespace(temper="temper", root=str(self.root / "install"), installation="study", model="splash-q4")
        study.package_root = self.root
        study.protocol = {"prepare_seconds": 600}
        study.limit = 27 * 1024**3
        study.remaining = lambda seconds: seconds
        source = self.root / "source.lock"
        source.write_bytes(b"future Temper lock: opaque to Field Kit\n")
        cell = {"preset": "splash-q4", "lock": source.name, "window": 32768, "kind": "context", "family": "splash"}
        directory = self.root / "cell"
        directory.mkdir()
        calls = []
        def run(argv, timeout):
            calls.append(argv)
            operation = argv[2]
            lock = directory / "execution.lock.json"
            execution = {"schema": "temper-execution/v1", "profile": "splash-q4", "layouts": ["splash-q4"],
                         "execution_digest": "a"*64, "lock_sha256": digest(b"configured opaque lock"),
                         "request_defaults": {"splash-q4": {}}}
            if operation == "configure":
                self.assertEqual(Path(argv[argv.index("--lock")+1]), source)
                self.assertEqual(argv[argv.index("--context")+1], "32768")
                self.assertEqual(argv[argv.index("--max-output")+1], "4096")
                self.assertEqual(argv[argv.index("--max-memory")+1], str(27 * 1024**3))
                lock.write_bytes(b"configured opaque lock")
                result = {"schema": "temper-execution-configuration/v1", "preset": "splash-q4",
                          "settings": execution_settings(cell, study.limit), "context_execution_sha256": "c"*64}
            elif operation == "inspect":
                result = execution
            elif operation == "prepare":
                result = {"schema": "temper-execution-material/v1", "execution": execution,
                          "generation": "b"*64, "binding": "schema: temper-field-kit-binding/v1\n"}
            elif operation == "paths":
                result = {"schema": "temper-execution-paths/v1", "execution": execution, "models": {}, "python": {}}
            else:
                self.fail("unexpected host operation: " + operation)
            return CommandResult(canonical_json(result), b"", 0)
        with patch("fieldkit_runtime.experiments.qwen.splash_study.run_process_silent", side_effect=run):
            material = study.configure(cell, directory)
        self.assertEqual(material["context_execution_sha256"], "c"*64)
        self.assertEqual([argv[2] for argv in calls], ["configure", "inspect", "prepare", "paths"])
        self.assertEqual(source.read_bytes(), b"future Temper lock: opaque to Field Kit\n")
        self.assertEqual([p.name for p in directory.iterdir()], ["execution.lock.json"])

    def test_grading_failure_preserves_submission_and_shutdown_authority(self):
        for safe in (True, False):
            with self.subTest(safe=safe):
                study=object.__new__(SplashStudy)
                study.safe_to_cleanup=True;study.stopped=False;study.package_root=PACKAGE
                delivered={"failure":None,"measurement_valid":True,"finish_reason":"tool_calls",
                           "message":{"tool_calls":[{"function":{"name":"submit_patch","arguments":"retained"}}]}}
                study.chat=lambda *args, **kwargs:copy.deepcopy(delivered)
                module="fieldkit_runtime.experiments.qwen.splash_study."
                with patch(module+"submission",return_value={}), patch(module+"unpack",return_value=self.root), \
                     patch(module+"apply",return_value={"changed_files":["src/flask/helpers.py"],"diff":"retained patch"}), \
                     patch(module+"evaluate",side_effect=EvaluationFailure("grader failed",safe_to_cleanup=safe)):
                    row=study.coding_case(None,{}, {"paths":{"python":{"coding-evaluator":"fixture"}}},
                                          {"edit_scope":{}},self.root)
                self.assertEqual(row["message"],delivered["message"])
                self.assertEqual(row["patch"]["diff"],"retained patch")
                self.assertEqual(row["status"],"invalid-evaluation")
                self.assertEqual(study.safe_to_cleanup,safe)
                self.assertTrue(study.stopped)

    def test_exact_buckets_and_no_midrange_inference(self):
        for gib in (32,35,37,40,47):
            with self.assertRaises(Refusal): bucket({"physical_memory_bytes":gib*1024**3})
        self.assertEqual(bucket({"physical_memory_bytes":36*1024**3}),"36")
        self.assertEqual(bucket({"physical_memory_bytes":128*1024**3}),"48-plus")
        small=matrix(PROTOCOL,{"physical_memory_bytes":36*1024**3})
        large=matrix(PROTOCOL,{"physical_memory_bytes":48*1024**3})
        self.assertEqual([c["preset"] for c in small if c["kind"]=="coding"],["splash-q4","splash-q5","splash-q6"])
        self.assertEqual(len([c for c in small if c["kind"]=="context"]),6)
        self.assertEqual(len(large),8)
        self.assertFalse(any(c["kind"]=="context" for c in large))
        self.assertTrue(large[-1]["candidate"])

    def test_fragmented_reasoning_and_tool_calls_preserve_partial_answers(self):
        stream=CodingStream(self.root/"response.sse",clock=lambda:4.0)
        events=[{"choices":[{"delta":{"reasoning":"thinking"}}]},
                {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call", "function":{"name":"submit_","arguments":"{\"a\":"}}]}}]},
                {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"patch","arguments":"1}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":4}}]
        wire=b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events)+b"data: [DONE]\n\n"
        result=stream(io.BytesIO(wire),1.0)
        self.assertEqual(result["message"]["reasoning"],"thinking")
        self.assertEqual(result["message"]["tool_calls"][0]["function"],{"name":"submit_patch","arguments":'{"a":1}'})
        self.assertTrue(result["stream_complete"])
        partial=CodingStream(self.root/"partial.sse",clock=lambda:4.0)
        with self.assertRaises(ValueError): partial(io.BytesIO(wire.split(b"data: [DONE]")[0]+b"data: invalid\n"),1.0)
        self.assertEqual(partial.snapshot()["message"],result["message"])
        self.assertFalse(partial.snapshot()["stream_complete"])

    def test_native_budget_and_missing_engine_timing_are_not_invented(self):
        case=json.loads((PACKAGE/"workloads.json").read_bytes())["cases"][0]
        body=request_body(case,"splash-q4",118000,16078)
        self.assertEqual(body["max_tokens"],101922)
        self.assertEqual(body["messages"],case["request"]["messages"])
        self.assertEqual(body["seed"],17)
        response={"stream_complete":True,"usage":{"prompt_tokens":16078,"completion_tokens":100},"service_seconds":10.0,"first_token_seconds":1.0}
        row=performance(response,16078)
        self.assertTrue(row["measurement_valid"])
        self.assertIsNone(row["prefill_tokens_per_second"])
        self.assertEqual(row["observed_output_tokens_per_second"],11)
        self.assertFalse(performance(response,16079)["measurement_valid"])
        with self.assertRaises(Exception): request_body(case,"splash-q4",118000,117000)

    def test_patch_scope_rejects_test_configuration_symlinks_and_ambiguous_json(self):
        base=unpack(PACKAGE/"flask.tar.gz",self.root/"fixture")
        case=json.loads((PACKAGE/"workloads.json").read_bytes())["cases"][0]
        for path in ("../escape.py","tests/conftest.py","tests/.hidden.py","/tmp/x.py"):
            with self.assertRaises(InvalidPatch): staged_texts({"edits":[],"new_files":[{"path":path,"content":"x"}]},base,case["edit_scope"])
        (base/"tests/test_candidate_async_stream_link.py").symlink_to(self.root/"outside")
        with self.assertRaises(InvalidPatch): staged_texts({"edits":[],"new_files":[{"path":"tests/test_candidate_async_stream_link.py","content":"x"}]},base,case["edit_scope"])
        with self.assertRaises(InvalidPatch): submission({"message":{"tool_calls":[{"function":{"name":"submit_patch","arguments":'{"edits":[],"edits":[]}'}}]}})

    def test_preview_checks_current_host_without_creating_session_or_lock(self):
        self.copy_catalog()
        local = self.root / ".local"
        local.mkdir()
        (local / "temper").write_bytes(b"fixture")
        args = argparse.Namespace(temper=None, preview=True, new=False)
        before = sorted(self.root.rglob("*"))
        for memory in (36, 48):
            raw = machine(memory)
            runner = MatrixRunner()
            with self.subTest(memory=memory), redirect_stdout(io.StringIO()):
                self.assertEqual(contribute(args, self.root,
                    input_fn=lambda _: self.fail("preview requested consent"),
                    facts_reader=lambda _: (parse_machine_facts(raw), raw), runner=runner), 0)
            self.assertEqual(sorted(self.root.rglob("*")), before)
            self.assertFalse(runner.actions)

    def run_study(self,memory=48,**settings):
        self.copy_catalog()
        (self.root/".local").mkdir();(self.root/".local/temper").write_bytes(b"fixture")
        args=argparse.Namespace(temper=None,preview=False,new=False)
        raw=machine(memory);runner=MatrixRunner(**settings);prompts=[]
        with patch("fieldkit_runtime.experiments.qwen.splash_contributor.shutil.disk_usage",return_value=SimpleNamespace(free=1024**4)),redirect_stdout(io.StringIO()):
            contribute(args,self.root,input_fn=lambda prompt:prompts.append(prompt) or "yes",facts_reader=lambda _:(parse_machine_facts(raw),raw),runner=runner)
        return args,runner,prompts

    def test_48_bucket_continues_after_oom_and_never_replays_completed_tasks(self):
        args,runner,prompts=self.run_study(fail="coding-rapid-mlx")
        self.assertEqual(len(prompts),1)
        self.assertIn("coding-vllm-metal",runner.actions)
        self.assertEqual(len(runner.requests),14)
        export=next((self.root/"runs").glob("*/result.json"))
        result=review(export,self.root/"catalog/packages/qwen-machine-study@4/package.json")
        rows=result["answers"]["completed-work"]["value"]["rows"]
        self.assertEqual(next(r for r in rows if r["id"]=="coding-rapid-mlx")["failure"]["kind"],"memory-failure")
        before=list(runner.requests)
        with redirect_stdout(io.StringIO()): contribute(args,self.root,input_fn=lambda _:self.fail("asked twice"),runner=runner)
        self.assertEqual(runner.requests,before)

    def test_context_stops_after_first_unsuccessful_point_then_runs_larger_quants(self):
        _,runner,_=self.run_study(memory=36,context_fails=True)
        self.assertEqual(runner.actions,["coding-splash-q4","context-32768","coding-splash-q5","coding-splash-q6","finish-study"])
        result=review(next((self.root/"runs").glob("*/result.json")),self.root/"catalog/packages/qwen-machine-study@4/package.json")
        self.assertIsNone(result["answers"]["context"]["value"]["highest_successful_window_tokens"])

    def test_uncertain_shutdown_prevents_later_cells_and_cleanup(self):
        with self.assertRaisesRegex(Refusal,"shutdown"): self.run_study(unsafe=True)
        path=next((self.root/".local").glob("*.session.json"));session=load_session(path)
        self.assertTrue(Path(session["paths"]["root"]).exists())
        self.assertEqual(len(session["answers"]["completed-work"]["value"]["rows"]),1)


if __name__=="__main__": unittest.main()
