"""A local engine Field Kit starts itself: bound, watched and provably stopped."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

from fieldkit_runtime import local_probe
from fieldkit_runtime.local_probe import LocalProbe, group_alive, port_open
from fieldkit_runtime.probe import ProbeError
from fieldkit_runtime.session import free_listen
from tests.test_watcher import watch_spec

ENGINE = r'''
import http.server, json, signal, subprocess, sys, time
port, mode = int(sys.argv[1]), sys.argv[2]
if mode == "exit":
    sys.exit(3)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "child":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", port), Health)
if mode == "escape":
    # A helper outside the engine's group keeps the listening socket open.
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                              pass_fds=[server.fileno()], start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open(sys.argv[3], "w").write(str(helper.pid))
server.serve_forever()
'''

class QuietWatcher:
    def __init__(self, *args, **kwargs):
        pass
    def run(self, stop):
        stop.wait()
        return {"state": "complete"}

class LocalProbeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = self.root / "engine.py"
        self.script.write_text(ENGINE)
        for name, value in (("run_text", ""), ("parse_swap_used_bytes", 0), ("parse_wired_bytes", 0)):
            patcher = patch.object(local_probe.watcher, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(local_probe.watcher, "CoordinatedWatcher", QuietWatcher)
        patcher.start()
        self.addCleanup(patcher.stop)

    def probe(self, mode, *extra):
        listen = free_listen()
        port = listen.rsplit(":", 1)[1]
        probe = LocalProbe(argv=[sys.executable, str(self.script), port, mode, *extra], environment={"PATH": "/usr/bin:/bin"},
                           listen=listen, log_dir=self.root / mode, watch_spec=watch_spec(), log_bytes_max=1024**2,
                           intent={"subject": "local", "executable": sys.executable, "listen": listen}, stop_seconds=1)
        self.addCleanup(lambda: probe.process and probe.process.poll() is None and probe.process.kill())
        return probe

    def ready(self, probe):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://{probe.listen}/health", timeout=1) as response:
                    return json.loads(response.read())
            except OSError:
                probe.ensure_healthy()
                time.sleep(0.05)
        self.fail("engine did not become ready")

    def status(self, probe):
        return json.loads(probe.status_path.read_text())

    def test_owned_engine_is_bound_watched_and_its_stop_proven(self):
        probe = self.probe("normal")
        probe.start()
        self.assertEqual(self.status(probe)["state"], "running")
        self.assertEqual(probe.full_binding["roles"][0]["id"], "engine")
        self.assertEqual(self.ready(probe), {"status": "ok"})
        probe.validate_owned_boundary()
        result = probe.finish()
        self.assertTrue(result["safe_to_cleanup"], result["issues"])
        self.assertEqual((self.status(probe)["state"], self.status(probe)["safe_to_cleanup"]), ("stopped", True))
        self.assertFalse(group_alive(probe.process.pid))
        self.assertFalse(port_open(probe.host, probe.port))

    def test_engine_ignoring_sigterm_has_its_own_group_killed(self):
        probe = self.probe("stubborn")
        probe.start()
        self.ready(probe)
        result = probe.finish()
        self.assertTrue(result["safe_to_cleanup"], result["issues"])
        self.assertTrue(any("killed the group it created" in issue for issue in result["issues"]))

    def test_children_in_the_engine_group_end_with_it(self):
        probe = self.probe("child")
        probe.start()
        self.ready(probe)
        self.assertTrue(probe.finish()["safe_to_cleanup"])
        self.assertFalse(group_alive(probe.process.pid))

    def test_a_helper_holding_the_port_outside_the_group_blocks_cleanup(self):
        marker = self.root / "helper.pid"
        probe = self.probe("escape", str(marker))
        probe.start()
        self.ready(probe)
        def stop_helper():
            import os, signal
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.addCleanup(stop_helper)
        result = probe.finish()
        self.assertFalse(result["safe_to_cleanup"])
        self.assertIn("engine port is still open", result["issues"])
        self.assertEqual(self.status(probe)["state"], "retained")

    def test_engine_that_exits_is_a_runtime_failure_not_a_hang(self):
        probe = self.probe("exit")
        try:
            probe.start()
            deadline = time.monotonic() + 5
            with self.assertRaisesRegex(ProbeError, "exited"):
                while time.monotonic() < deadline:
                    probe.ensure_healthy()
                    time.sleep(0.05)
        except ProbeError as error:
            self.assertIn("exited", str(error))
        self.assertTrue(probe.finish()["safe_to_cleanup"])
