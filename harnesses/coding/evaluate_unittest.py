"""Run frozen and authored unittest files in the existing macOS sandbox model."""
import json
from pathlib import Path
import subprocess
import time

GROUP_TIMEOUT = 30
LOG_LIMIT = 8 * 1024**2
BOOTSTRAP = r'''
import importlib.util, json, pathlib, sys, unittest
expected = pathlib.Path(sys.argv[1]).resolve()
report = pathlib.Path(sys.argv[2])
import pagination
assert pathlib.Path(pagination.__file__).resolve() == expected
suite = unittest.TestSuite()
for index, filename in enumerate(sys.argv[3:]):
    spec = importlib.util.spec_from_file_location("frozen_test_" + str(index), filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
result = unittest.TextTestRunner(verbosity=2).run(suite)
summary = {"tests": result.testsRun, "failures": len(result.failures),
           "errors": len(result.errors), "skipped": len(result.skipped)}
report.write_text(json.dumps(summary))
raise SystemExit(0 if result.wasSuccessful() and result.testsRun > len(result.skipped) else 1)
'''


class EvaluationFailure(RuntimeError):
    def __init__(self, message, *, safe_to_cleanup):
        super().__init__(message)
        self.safe_to_cleanup = safe_to_cleanup


def group_passes(group):
    summary = group.get("summary") or {}
    return (group.get("exit_code") == 0 and not group.get("stopped")
            and summary.get("tests", 0) > summary.get("skipped", 0))


def _run_group(candidate, out, label, tests, python, oracle, baseline, *, deadline=None, observer=None):
    candidate, baseline, oracle = (Path(p).resolve() for p in (candidate, baseline, oracle))
    destination = (Path(out) / label).resolve()
    destination.mkdir(parents=True)
    timeout = min(GROUP_TIMEOUT, deadline - time.time()) if deadline else GROUP_TIMEOUT
    if timeout <= 0:
        raise TimeoutError("Coding time budget exhausted before testing")
    allowed = [candidate, destination, baseline, oracle, Path(python).resolve().parent.parent,
               Path(python).absolute().parent.parent, Path('/System'), Path('/usr/lib'),
               Path('/usr/share'), Path('/private/etc'), Path('/dev')]
    reads = " ".join("(subpath " + json.dumps(str(path)) + ")" for path in allowed)
    profile = ('(version 1) (allow default) (deny network*) (deny process-fork) '
               '(deny file-read*) (allow file-read-metadata) (allow file-read* (literal "/") '
               + reads + ') (deny file-write*) (allow file-write* (subpath '
               + json.dumps(str(destination)) + ') (literal "/dev/null"))')
    (destination / 'sandbox.sb').write_text(profile)
    report, log = destination / 'counts.json', destination / 'output.txt'
    argv = ['/usr/bin/sandbox-exec', '-f', str(destination / 'sandbox.sb'), str(python),
            '-B', '-c', BOOTSTRAP, str(candidate / 'pagination.py'), str(report), *map(str, tests)]
    env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(candidate), 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(destination), 'LC_ALL': 'en_US.UTF-8'}
    (destination / 'invocation.json').write_text(json.dumps({'argv': argv, 'env': env,
        'cwd': str(destination), 'timeout_seconds': timeout}, indent=2) + '\n')
    started, stopped, interrupted = time.monotonic(), None, None
    remaining = []
    with log.open('wb') as output:
        process = subprocess.Popen(argv, cwd=destination, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if observer:
                    observer()
                if time.monotonic() - started > timeout or log.stat().st_size > LOG_LIMIT:
                    stopped = 'deadline' if time.monotonic() - started > timeout else 'output-limit'
                    break
                time.sleep(.05)
        except BaseException as error:
            interrupted, stopped = error, type(error).__name__
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            try:
                rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid='], text=True, timeout=5)
                remaining = [line.strip() for line in rows.splitlines()
                             if len(line.split()) == 2 and int(line.split()[1]) == process.pid]
            except Exception as error:
                raise EvaluationFailure('Cannot verify test cleanup: ' + str(error), safe_to_cleanup=False) from error
    summary = json.loads(report.read_text()) if report.exists() else None
    result = {'exit_code': process.returncode, 'seconds': time.monotonic() - started,
              'stopped': stopped, 'summary': summary, 'output': str(log),
              'safe_to_cleanup': not remaining, 'remaining_processes': remaining}
    (destination / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    if remaining:
        raise EvaluationFailure('Unexpected test descendants remain', safe_to_cleanup=False)
    if interrupted:
        raise interrupted
    return result


def evaluate(candidate_path, output_dir, case, fixture, python, *, visible_only=False,
             deadline=None, observer=None, require_candidate_tests=True):
    candidate, fixture = Path(candidate_path).resolve(), Path(fixture).resolve()
    baseline = fixture / 'base'
    out = Path(output_dir).resolve()
    if out == candidate or candidate in out.parents:
        raise ValueError('Test output must be outside candidate source')
    out.mkdir(parents=True, exist_ok=False)
    original = [baseline / name for name in case['original_tests']]
    authored = sorted((candidate / 'tests').glob('test_candidate_*.py'))
    oracle = fixture / case['oracle']
    groups = {'original': _run_group(candidate, out, 'original', original, python,
                                     baseline / 'tests', baseline, deadline=deadline, observer=observer)}
    if authored:
        groups['candidate'] = _run_group(candidate, out, 'candidate', authored, python,
                                         baseline / 'tests', baseline, deadline=deadline, observer=observer)
    else:
        groups['candidate'] = {'exit_code': None, 'seconds': 0, 'output': None,
                               'summary': None, 'status': 'missing', 'required': require_candidate_tests}
    if not visible_only:
        groups['hidden'] = _run_group(candidate, out, 'hidden', [oracle], python, oracle.parent,
                                      baseline, deadline=deadline, observer=observer)
    behavioral = ['original'] if visible_only else ['original', 'hidden']
    count = groups['candidate'].get('summary') or {}
    result = {'case_id': case['id'], 'candidate': str(candidate), 'groups': groups,
              'valid': all(groups[name]['summary'] is not None and not groups[name]['stopped'] for name in behavioral),
              'behavior_pass': all(group_passes(groups[name]) for name in behavioral),
              'require_candidate_tests': require_candidate_tests,
              'candidate_test_cases_executed': count.get('tests', 0) - count.get('skipped', 0),
              'visible_only': visible_only}
    result['all_pass'] = result['behavior_pass'] and (group_passes(groups['candidate']) or not require_candidate_tests)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
