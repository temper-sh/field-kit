#!/usr/bin/env python3
"""Extract the selected Flask workload from the retained Labs comparison.

Authoring only; participants never depend on a Labs checkout. Inputs are exact
local sources; this command performs no downloads or inference.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zlib


def extract(result_path, source, destination):
    result = json.loads(result_path.read_text())
    commit = result['method']['base']['commit']
    if commit != '49b7e7bc8fb69d605719991d1c0a99fcee689053':
        raise ValueError('unexpected Flask base')
    destination.mkdir(parents=True, exist_ok=True)
    cases = []
    members = {}
    # The source manifest belongs to the original task author, not generated
    # output. Verify it once when extracting; do not port its transport hashes.
    manifest = json.loads((source.parent / 'manifest.json').read_text())
    for name, sha in manifest['immutable_files'].items():
        if not name.startswith('base/'):
            continue
        path = source / name.removeprefix('base/')
        data = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError('original source differs: ' + name)
        members[name] = data
    for name, case in result['cases'].items():
        packed = case['request']
        raw = zlib.decompress(base64.b64decode(packed['data']))
        if hashlib.sha256(raw).hexdigest() != packed['sha256']:
            raise ValueError('retained request differs')
        body = json.loads(raw)
        for key in ('model', 'max_tokens'):
            body.pop(key, None)
        oracle = 'oracles/' + name + '/' + Path(case['independent_oracle']['file']).name
        members[oracle] = case['independent_oracle']['source'].encode()
        cases.append({'id': name, 'request': body, 'edit_scope': case['edit_scope'],
                      'original_tests': case['original_tests'], 'oracle': oracle,
                      'candidate_test_prefix': 'tests/test_candidate_' + name.replace('-', '_')})
    workload = {'schema': 'field-kit-qwen-coding/v1', 'base_commit': commit,
                'source': 'Labs qwen27-splash-frog-m5, observed 2026-09-26', 'cases': cases}
    (destination / 'workloads.json').write_text(json.dumps(workload, indent=2) + '\n')
    import gzip
    with (destination / 'flask.tar.gz').open('wb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, data in sorted(members.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
                    archive.addfile(entry, io.BytesIO(data))
    (destination / 'THIRD-PARTY.md').write_text(
        '# Flask coding fixture\n\nFlask commit `' + commit + '`, from '
        'https://github.com/pallets/flask. The source and selected task context '
        'retain the BSD-3-Clause license in `base/LICENSE.txt` inside the archive.\n\n'
        + result['method']['base']['license'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--flask-source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    extract(args.result, args.flask_source, args.out)
