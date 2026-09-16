"""Bootstrap refuses bad release bytes before invoking an installer."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupTest(unittest.TestCase):
    def test_bad_download_is_not_extracted_or_installed_and_retry_lock_is_released(self):
        with tempfile.TemporaryDirectory(prefix="field kit bootstrap ") as temporary:
            root = Path(temporary)
            shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
            commands = root / "commands"
            commands.mkdir()
            (commands / "uname").write_text('#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n')
            (commands / "curl").write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then shift; printf bad-release > "$1"; exit 0; fi; shift; done\nexit 1\n')
            (commands / "ditto").write_text('#!/bin/sh\ntouch "' + str(root / "incorrect-extraction") + '"\nexit 1\n')
            for path in commands.iterdir(): path.chmod(0o755)
            result = subprocess.run([str(root / "setup.sh"), "--install-only"], cwd="/",
                env={**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum mismatch", result.stderr)
            self.assertFalse((root / "incorrect-extraction").exists())
            self.assertFalse((root / ".local/temper").exists())
            self.assertFalse((root / ".local/setup.lock").exists())
            self.assertFalse(list((root / ".local").glob(".setup.*")))
