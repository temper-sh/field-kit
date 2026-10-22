"""Field Kit's coding harness: supplied-source repair tasks with sandboxed tests.

Field Kit serves the preset and owns its processes, deadlines and memory. This
harness owns the conversation, the complete proposals and their grading. Run it
through a command workload (see README.md); `verify` needs no model.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

import bootstrap
from client import Client, save
import evaluate_flask
import evaluate_unittest
import patches
import quality
from fieldkit_runtime.process import runtime_identity

ROOT = Path(__file__).resolve().parent
GRADING = bootstrap.FIELD_KIT_ROOT / '.local/grading/bin/python'
TASKS = {
    'pagination-repair': {'pack': 'pagination', 'case': 'pagination-repair', 'archive': 'fixture.tar.gz',
                          'evaluation': evaluate_unittest, 'seconds': 900, 'grading': False},
    'flask-async-stream': {'pack': 'flask', 'case': 'async-stream', 'archive': 'flask.tar.gz',
                           'evaluation': evaluate_flask, 'seconds': 1800, 'grading': True},
    'flask-template-decorators': {'pack': 'flask', 'case': 'template-decorators', 'archive': 'flask.tar.gz',
                                  'evaluation': evaluate_flask, 'seconds': 1800, 'grading': True},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_files():
    """Task inputs whose bytes define a task revision: sources, tests, oracles, review checks, references."""
    return sorted(path for path in (ROOT / 'tasks').rglob('*') if path.is_file() and '__pycache__' not in path.parts)


def source_identity():
    return {str(path.relative_to(ROOT)): sha(path) for path in [*sorted(ROOT.glob('*.py')), *frozen_files()]}


def load_case(task):
    spec = TASKS[task]
    cases = json.loads((ROOT / 'tasks' / spec['pack'] / 'cases.json').read_text())['cases']
    return next(case for case in cases if case['id'] == spec['case'])


def review_checks(task, case):
    folder = ROOT / 'tasks' / TASKS[task]['pack'] / 'review' / case['id']
    return folder, sorted(folder.glob('test_*.py')) if folder.is_dir() else []


def verify(write_manifest=False):
    """Frozen inputs match the manifest, and each reference proposal applies to its original source."""
    actual = {str(path.relative_to(ROOT)): sha(path) for path in frozen_files()}
    if write_manifest:  # only for a deliberate new task revision
        (ROOT / 'manifest.json').write_text(json.dumps({'files': actual}, indent=2) + '\n')
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    if actual != manifest['files']:
        changed = sorted(set(actual.items()) ^ set(manifest['files'].items()))
        raise RuntimeError(f'Frozen task inputs differ from manifest.json: {[name for name, _ in changed]}')
    import tempfile
    references = {}
    with tempfile.TemporaryDirectory() as temporary:
        for task, spec in TASKS.items():
            case = load_case(task)
            proposal = json.loads((ROOT / 'tasks' / spec['pack'] / 'reference' / f"{case['id']}.json").read_text())['proposal']
            fixture = Path(temporary) / task
            base = evaluate_flask.unpack(ROOT / 'tasks' / spec['pack'] / spec['archive'], fixture)
            changed, _ = patches.staged_texts(proposal, base, case['edit_scope'])
            if not (fixture / case['oracle']).is_file():
                raise RuntimeError(f'{task}: independent tests are missing from the fixture')
            references[task] = sorted(changed)
    return {'verified': True, 'tasks': sorted(TASKS), 'reference_changes': references, 'inference': False}


def grade(output, task, case, fixture, python, seconds=180):
    """Grade every distinct complete proposal; review checks and independent tests never reach the model."""
    evaluation = TASKS[task]['evaluation']
    review_folder, reviews = review_checks(task, case)
    assessments, seen = [], set()
    deadline = time.time() + seconds
    for stage in sorted(output.glob('turn-???')):
        path = stage / 'response.json'
        if not path.exists():
            continue
        response = json.loads(path.read_text())
        if not response.get('stream_complete') or response.get('finish_reason') not in ('tool_calls', 'stop'):
            continue
        calls = response['message'].get('tool_calls') or []
        if len(calls) != 1 or calls[0]['function']['name'] not in ('test_patch', 'submit_patch'):
            continue
        try:
            proposal = patches.submission(response, tool=calls[0]['function']['name'])
            signature = hashlib.sha256(json.dumps({k: v for k, v in proposal.items() if k != 'explanation'},
                                                  sort_keys=True).encode()).hexdigest()
            if signature in seen:
                assessments.append({'turn': stage.name, 'same_patch_as_prior': True, 'patch_identity': signature})
                continue
            dest = output / ('assessment-' + stage.name)
            dest.mkdir()
            applied = patches.apply(proposal, fixture / 'base', dest / 'candidate', case['edit_scope'])
            (dest / 'proposal.patch').write_text(applied['diff'])
            result = evaluation.evaluate(dest / 'candidate', dest / 'tests', case, fixture, str(python), deadline=deadline)
            assessment = {'turn': stage.name, 'patch_identity': signature, 'changed_files': applied['changed_files'],
                          'evaluation': result}
            if reviews:
                assessment['review'] = evaluation._run_group(dest / 'candidate', dest, 'review', [str(p) for p in reviews],
                                                             str(python), review_folder, fixture / 'base', deadline=deadline)
            assessments.append(assessment)
            seen.add(signature)
        except patches.InvalidPatch as error:
            assessments.append({'turn': stage.name, 'invalid_patch': str(error)})
    return assessments


def grading_identity(python):
    marker = Path(python).absolute().parent.parent / 'field-kit-grading.json'
    return {'python': str(Path(python).absolute()),
            'environment': json.loads(marker.read_text()) if marker.is_file() else 'Field Kit Python, standard library only'}


def run(args):
    spec = TASKS[args.task]
    output = Path(os.environ['FIELD_KIT_OUTPUT_DIR']).resolve()
    if any(output.iterdir()):
        raise ValueError('Every attempt needs an empty output directory')
    context = args.context or int(os.environ.get('FIELD_KIT_CONTEXT_WINDOW_TOKENS') or 0) or None
    if context is None and not args.output_tokens:
        raise ValueError('The context window is unknown; pass --context or --output-tokens')
    python = args.grading_python or (GRADING if spec['grading'] else Path(sys.executable))
    if not Path(python).is_file():
        raise ValueError(f'Grading Python is missing: {python}; run ./field-kit grading first')
    base, native, model = (os.environ[name] for name in ('FIELD_KIT_BASE_URL', 'FIELD_KIT_NATIVE_URL', 'FIELD_KIT_MODEL'))
    sampling = {**args.sampling, 'seed': args.seed}
    client = Client(base, model, context, sampling, native_root=native,
                    model_adapter=os.environ['FIELD_KIT_MODEL_ADAPTER'], output_tokens=args.output_tokens)
    case = load_case(args.task)
    seconds = args.seconds or spec['seconds']
    row = {'state': 'running', 'started_epoch': time.time(), 'task': args.task, 'case': case['id'],
           'endpoint': base, 'native_endpoint': native, 'model': model, 'model_adapter': client.adapter.identifier,
           'context_tokens': context, 'fixed_output_tokens': args.output_tokens,
           'sampling_overrides': sampling, 'sampling_default': 'the preset\'s own',
           'contract': args.contract, 'system_sha256': hashlib.sha256(quality.system_text(args.contract).encode()).hexdigest(),
           'field_kit': runtime_identity(), 'grading': grading_identity(python), 'source_sha256': source_identity(),
           'budget_seconds': seconds, 'max_turns': args.turns, 'quality_measured': False,
           'source_review': 'pending', 'failure': None}
    fixture = output / 'fixture'
    evaluate_flask.unpack(ROOT / 'tasks' / spec['pack'] / spec['archive'], fixture)
    save(output / 'case.json', case)
    save(output / 'result.json', row)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('operator stop')))
    interrupted = False
    try:
        deadline = time.time() + seconds
        def request(stage, body, turn):
            print(json.dumps({'task': args.task, 'turn': turn['number'], 'seconds_left': round(deadline - time.time())}), flush=True)
            return client.request(stage, body, time.monotonic() + max(0, deadline - time.time()))
        quality.run(case, output, fixture, str(python), args.turns, deadline, request,
                    lambda response: {'usage': response.get('usage'), 'timings': response.get('timings')}, row,
                    model_adapter=client.adapter, evaluation=spec['evaluation'], contract=args.contract)
        row['state'] = row['quality']['status']
    except KeyboardInterrupt as error:
        interrupted = True
        row.update(state='interrupted', failure={'kind': 'operator-stop', 'message': str(error)})
    except Exception as error:
        row.update(state='incomplete' if isinstance(error, TimeoutError) else 'integration-failed',
                   failure={'kind': type(error).__name__, 'message': str(error)})
    finally:
        if not interrupted:
            try:
                row['assessments'] = grade(output, args.task, case, fixture, python)
                row['quality_measured'] = any('evaluation' in a for a in row['assessments'])
            except Exception as error:
                row['grading_error'] = type(error).__name__ + ': ' + str(error)
        row['finished_epoch'] = time.time()
        save(output / 'result.json', row)
    print(json.dumps({'task': args.task, 'state': row['state'], 'quality_measured': row['quality_measured']}), flush=True)
    return 0 if row['state'] == 'submitted' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    task = commands.add_parser('run', help='one task against the preset Field Kit serves')
    task.add_argument('task', choices=sorted(TASKS))
    task.add_argument('--seconds', type=int, help='task wall-clock budget; default 900 for pagination, 1800 for Flask')
    task.add_argument('--turns', type=int, default=6, help='responses, including the final submission')
    task.add_argument('--context', type=int, help="override Field Kit's FIELD_KIT_CONTEXT_WINDOW_TOKENS")
    task.add_argument('--contract', choices=sorted(quality.CONTRACTS), default='original',
                      help='labelled prompt treatment added to the system text')
    task.add_argument('--sampling', type=json.loads, default={},
                      help='JSON sampling overrides, e.g. \'{"temperature": 1, "top_k": 20}\'; default the preset\'s own')
    task.add_argument('--seed', type=int, default=421)
    task.add_argument('--output-tokens', type=int, help='fixed output allowance for an engine without token routes')
    task.add_argument('--grading-python', type=Path, help='default: .local/grading for Flask, Field Kit Python otherwise')
    check = commands.add_parser('verify', help='check frozen task inputs and reference proposals; no model')
    check.add_argument('--write-manifest', action='store_true', help='record a deliberate new task revision')
    args = parser.parse_args(argv)
    if args.command == 'verify':
        print(json.dumps(verify(args.write_manifest), indent=2))
        return 0
    if min(args.turns, args.seconds or 1, args.output_tokens or 1) <= 0 or not isinstance(args.sampling, dict):
        parser.error('turns, seconds and output tokens must be positive; sampling is a JSON object')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
