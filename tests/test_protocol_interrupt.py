"""A real child gets time to save its result when its user presses Ctrl-C."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class ProtocolInterruptTest(unittest.TestCase):
    def test_protocol_commits_partial_result_before_the_runner_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = root / "protocol.py"
            child.write_text('''import signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
def stop(signum, frame):
    (root / "partial-result").write_text("observations saved; owned shutdown checked")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
(root / "ready").touch()
while True: time.sleep(0.1)
''')
            helper = 'import json, sys; from fieldkit_runtime.workflow import run_contributor_process; result=run_contributor_process(sys.argv[1:], 20); print(json.dumps({"returncode":result.returncode})); sys.exit(result.returncode)'
            process = subprocess.Popen([sys.executable, "-c", helper, sys.executable, str(child), str(root), "--action", "fixture", "--field-kit-runtime", "fixture"],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 10
                while not (root / "ready").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "ready").exists())
                process.send_signal(signal.SIGINT)
                output, error = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, error)
                self.assertEqual(json.loads(output.splitlines()[-1])["returncode"], 0)
                self.assertIn("observations saved", (root / "partial-result").read_text())
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=10)
