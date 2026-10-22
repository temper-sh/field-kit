"""Field Kit asks the installed Temper once for its own pinned Python, never a system one."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("plutil"), "the launcher reads Temper's JSON with macOS plutil")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="field kit launcher ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "field-kit"
        self.root.mkdir()
        shutil.copy2(ROOT / "field-kit", self.root / "field-kit")
        shutil.copytree(ROOT / "fieldkit_runtime", self.root / "fieldkit_runtime")
        shutil.copytree(ROOT / "runtime", self.root / "runtime")
        self.commands = Path(temporary.name) / "commands"
        self.commands.mkdir()
        # A python3 on PATH that must never run.
        self.marker = Path(temporary.name) / "path-python-ran"
        (self.commands / "python3").write_text(f"#!/bin/sh\ntouch '{self.marker}'\nexit 1\n")
        (self.commands / "python3").chmod(0o755)
        managed = Path(temporary.name) / "managed/bin"
        managed.mkdir(parents=True)
        (managed / "python3").symlink_to(sys.executable)
        self.check = json.dumps({"status": "exact", "units": [{"location": str(managed.parent)}]})

    def launch(self, *arguments, temper=True, python=None):
        if temper:
            script = self.commands / "temper"
            script.write_text('#!/bin/sh\necho "$@" >> "' + str(self.commands / "calls") + '"\n'
                              'case "$2" in check) printf %s \'' + self.check + '\' ;; esac\n')
            script.chmod(0o755)
        environment = {key: value for key, value in os.environ.items() if key != "FIELD_KIT_PYTHON"}
        environment["PATH"] = str(self.commands) + os.pathsep + "/usr/bin:/bin"
        if python:
            environment["FIELD_KIT_PYTHON"] = python
        return subprocess.run([str(self.root / "field-kit"), *arguments], capture_output=True, text=True, env=environment)

    def calls(self):
        path = self.commands / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def test_first_run_installs_the_pinned_python_through_temper_once(self):
        first = self.launch("version")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("installing Field Kit's own Python through Temper", first.stderr)
        self.assertIn('"version"', first.stdout)
        calls = self.calls()
        self.assertEqual([call.split()[:2] for call in calls], [["software", "install"], ["software", "check"]])
        self.assertIn("--root " + str(self.root.resolve() / ".local/python"), calls[0])
        self.assertIn("runtime/python-macos-arm64.lock.yaml", calls[0])
        second = self.launch("version")
        self.assertEqual((second.returncode, second.stderr), (0, ""))
        self.assertEqual(len(self.calls()), 2)
        self.assertFalse(self.marker.exists())

    def test_without_temper_it_says_how_to_install_it_and_uses_no_other_python(self):
        result = self.launch("version", temper=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew install temper-sh/temper/temper", result.stderr)
        self.assertFalse((self.root / ".local/python-path").exists())
        self.assertFalse(self.marker.exists())

    def test_development_override_must_be_python_3_14_or_newer(self):
        old = self.commands / "old-python"
        old.write_text("#!/bin/sh\nexit 1\n")
        old.chmod(0o755)
        result = self.launch("version", python=str(old))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.14 or newer", result.stderr)
        self.assertEqual(self.calls(), [])
        accepted = self.launch("--help", python=sys.executable)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertFalse((self.root / ".local").exists())


if __name__ == "__main__":
    unittest.main()
