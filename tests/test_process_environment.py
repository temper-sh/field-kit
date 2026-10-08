"""Temper can find the user's cache and download client through study processes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fieldkit_runtime.workflow import (
    capture_process,
    run_contributor_process,
    run_process_silent,
)


ROOT = Path(__file__).resolve().parents[1]


class ProcessEnvironmentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="field kit environment ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        commands = self.root / "user bin"
        commands.mkdir()
        for name in ("hf", "uv"):
            executable = commands / name
            executable.write_text(f"#!/bin/sh\nprintf '{name} available\\n'\n")
            executable.chmod(0o755)
        self.environment = {
            "HOME": str(self.root / "home"),
            "PATH": str(commands),
            "HF_HUB_CACHE": str(self.root / "shared hub"),
            "HF_HOME": str(self.root / "hf home"),
            "HUGGINGFACE_HUB_CACHE": str(self.root / "legacy hub"),
            "XDG_CACHE_HOME": str(self.root / "xdg cache"),
            "HF_TOKEN": "synthetic-test-token",
        }
        self.host = self.root / "host.py"
        self.host.write_text('''import json, os, subprocess
print(json.dumps({
    "environment": {key: os.environ.get(key) for key in
        ("HOME", "PATH", "HF_HUB_CACHE", "HF_HOME", "HUGGINGFACE_HUB_CACHE", "XDG_CACHE_HOME", "HF_TOKEN")},
    "clients": {name: subprocess.check_output([name], text=True).strip() for name in ("hf", "uv")},
}))
''')

    def test_host_commands_retain_cache_settings_and_find_user_installed_clients(self):
        for runner in (capture_process, run_process_silent, run_contributor_process):
            with self.subTest(runner=runner.__name__), patch.dict(os.environ, self.environment, clear=True):
                result = runner([sys.executable, "-B", "-S", str(self.host)], 10)

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                observed = json.loads(result.stdout)
                self.assertEqual(observed["environment"], self.environment)
                self.assertEqual(observed["clients"], {"hf": "hf available", "uv": "uv available"})

    def test_protocol_preserves_environment_for_later_temper_preparations(self):
        observation = self.root / "observation.json"
        protocol = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fieldkit_runtime.workflow import run_process_silent
result = run_process_silent([sys.executable, "-B", "-S", sys.argv[2]], 10)
Path(sys.argv[3]).write_bytes(result.stdout)
sys.exit(result.returncode)
'''
        with patch.dict(os.environ, self.environment, clear=True):
            result = run_contributor_process([
                sys.executable, "-B", "-S", "-c", protocol, str(ROOT), str(self.host), str(observation),
                "--action", "fixture", "--field-kit-runtime", str(ROOT),
            ], 20)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        observed = json.loads(observation.read_bytes())
        self.assertEqual(observed["environment"], self.environment)
        self.assertEqual(observed["clients"], {"hf": "hf available", "uv": "uv available"})
