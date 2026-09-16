from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class LauncherTest(unittest.TestCase):
    def run_cli(self, *arguments, env=None):
        return subprocess.run([str(ROOT/'field-kit'), *arguments], capture_output=True, text=True,
                              env={**os.environ, 'FIELD_KIT_PYTHON': sys.executable, **(env or {})})

    def test_version_and_read_only_discovery(self):
        for arguments in (('version',), ('verify',), ('--help',), ()):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_explicit_python_is_refused(self):
        result = self.run_cli('verify', env={'FIELD_KIT_PYTHON': '/does/not/exist'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FIELD_KIT_PYTHON', result.stderr)

    def test_no_python_runtime_install_is_implicit(self):
        result = self.run_cli('verify', env={'FIELD_KIT_PYTHON': '/usr/bin/false'})
        self.assertNotEqual(result.returncode, 0)

    def test_bundle_works_from_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([str(ROOT/'field-kit'), 'verify'], cwd=temporary,
                                    env={**os.environ, 'FIELD_KIT_PYTHON': sys.executable}, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
