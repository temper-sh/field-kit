"""Field Kit finds the installed Temper and asks it once for its own Python."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fieldkit_runtime.common import Refusal
from fieldkit_runtime.temper import locate

ROOT = Path(__file__).resolve().parents[1]


class LocateTest(unittest.TestCase):
    def test_temper_on_path_resolves_through_a_link_to_its_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            cellar, bin = Path(temporary) / "Cellar/temper/0.3.0/bin", Path(temporary) / "bin"
            cellar.mkdir(parents=True)
            bin.mkdir()
            binary = cellar / "temper"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            (bin / "temper").symlink_to(binary)
            path = os.environ["PATH"]
            os.environ["PATH"] = str(bin)
            try:
                self.assertEqual(locate(), binary.resolve())
            finally:
                os.environ["PATH"] = path
            self.assertEqual(locate(bin / "temper"), binary.resolve())

    def test_missing_temper_says_how_to_install_it(self):
        path = os.environ["PATH"]
        os.environ["PATH"] = tempfile.gettempdir() + "/no-such-folder"
        try:
            with self.assertRaisesRegex(Refusal, "brew install temper-sh/temper/temper"):
                locate()
        finally:
            os.environ["PATH"] = path


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
        python = Path(temporary.name) / "python/bin"
        python.mkdir(parents=True)
        (python / "python3").symlink_to(sys.executable)
        self.check = json.dumps({"status": "exact", "units": [{"location": str(python.parent)}]})

    def launch(self, *arguments, temper=True):
        if temper:
            script = self.commands / "temper"
            script.write_text('#!/bin/sh\necho "$@" >> "' + str(self.commands / "calls") + '"\n'
                              'case "$2" in check) printf %s \'' + self.check + '\' ;; esac\n')
            script.chmod(0o755)
        environment = {key: value for key, value in os.environ.items() if key != "FIELD_KIT_PYTHON"}
        environment["PATH"] = str(self.commands) + os.pathsep + "/usr/bin:/bin"
        return subprocess.run([str(self.root / "field-kit"), *arguments], capture_output=True, text=True, env=environment)

    def test_first_run_installs_python_through_temper_once(self):
        first = self.launch("version")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("installing Field Kit's own Python through Temper", first.stderr)
        self.assertTrue(first.stdout.startswith("field-kit "))
        calls = (self.commands / "calls").read_text().splitlines()
        self.assertEqual([call.split()[:2] for call in calls], [["software", "install"], ["software", "check"]])
        self.assertIn("--root " + str(self.root.resolve() / ".local/python"), calls[0])
        second = self.launch("version")
        self.assertEqual((second.returncode, second.stderr), (0, ""))
        self.assertEqual(len((self.commands / "calls").read_text().splitlines()), 2)

    def test_without_temper_it_says_how_to_install_it(self):
        result = self.launch("version", temper=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew install temper-sh/temper/temper", result.stderr)
        self.assertFalse((self.root / ".local/python-path").exists())


if __name__ == "__main__":
    unittest.main()
