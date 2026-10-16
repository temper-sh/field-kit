"""Temper can find the user's cache and download client through Field Kit."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fieldkit_runtime.process import capture_process, run_process_silent


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
        for runner in (capture_process, run_process_silent):
            with self.subTest(runner=runner.__name__), patch.dict(os.environ, self.environment, clear=True):
                result = runner([sys.executable, "-B", "-S", str(self.host)], 10)

                self.assertEqual(result.returncode, 0, result.stderr.decode())
                observed = json.loads(result.stdout)
                self.assertEqual(observed["environment"], self.environment)
                self.assertEqual(observed["clients"], {"hf": "hf available", "uv": "uv available"})

    def test_private_model_cache_reaches_host_without_mutating_caller(self):
        scoped = {**self.environment, "HF_HUB_CACHE": str(self.root / "owned hub")}
        with patch.dict(os.environ, self.environment, clear=True):
            result = run_process_silent([sys.executable, "-B", "-S", str(self.host)], 10, environment=scoped)
            self.assertEqual(dict(os.environ), self.environment)

        self.assertEqual(result.returncode, 0, result.stderr.decode())
        observed = json.loads(result.stdout)
        self.assertEqual(observed["environment"], scoped)
        self.assertEqual(observed["clients"], {"hf": "hf available", "uv": "uv available"})
