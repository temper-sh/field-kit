import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fieldkit_runtime.probe import ManagedProbe, ProbeError, split_listen
from tests.test_watcher import watch_spec, binding


class ProbeBoundaryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.probe = ManagedProbe(temper=Path("/fixture/temper"), root=root,
            installation="fixture", execution_lock=root / "execution.lock.json", generation="a" * 64,
            listen="127.0.0.1:18080", log_dir=root, watch_spec=watch_spec(), router_ready_seconds=30, log_bytes_max=1024)
        self.probe.process = Mock(pid=1234, returncode=0)
        self.probe.process.poll.return_value = 0
        self.status = {"schema": "temper-probe-status/v1", "state": "stopped", "temper_pid": 1234,
                       "root": str(root), "installation": "fixture", "generation": "a" * 64,
                       "listen": "127.0.0.1:18080", "process_group_id": binding()["process_group_id"],
                       "roles": binding()["roles"], "listeners_verified": False, "safe_to_cleanup": True,
                       "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def publish(self):
        self.probe.status_path.write_text(json.dumps(self.status))

    def test_listener_is_exact_loopback(self):
        self.assertEqual(split_listen("127.0.0.1:18080"), ("127.0.0.1", 18080))
        with self.assertRaises(ProbeError): split_listen("0.0.0.0:18080")

    def test_observation_stop_and_confirmed_shutdown_have_distinct_meanings(self):
        class StoppedWatch:
            thread = SimpleNamespace(is_alive=lambda: False)
            def finish(self): raise ProbeError("resource limit reached")
        self.probe.full_watch = StoppedWatch()
        self.publish()
        result = self.probe.finish()
        self.assertTrue(result["safe_to_cleanup"])
        self.assertIn("resource limit reached", result["issues"])

    def test_exited_temper_without_final_proof_never_permits_cleanup(self):
        self.assertFalse(self.probe.finish()["safe_to_cleanup"])
        for change in ({"safe_to_cleanup": False}, {"state": "running"}, {"temper_pid": 9999}):
            original = dict(self.status)
            self.status.update(change); self.publish()
            with self.subTest(change=change): self.assertFalse(self.probe.finish()["safe_to_cleanup"])
            self.status = original

    def test_stale_or_rebound_live_identity_is_refused(self):
        self.status.update(state="running", listeners_verified=True, safe_to_cleanup=False)
        self.probe.full_binding = binding(); self.publish()
        self.probe.validate_owned_boundary()
        self.status["roles"][0]["ps_lstart"] = "different start"
        self.publish()
        with self.assertRaises(ProbeError): self.probe.validate_owned_boundary()
        self.status["updated_at"] = "2000-01-01T00:00:00+00:00"; self.publish()
        with self.assertRaisesRegex(ProbeError, "stale"): self.probe._status()

    def test_stop_only_signals_its_temper_child(self):
        self.probe.process.poll.return_value = None
        self.probe.request_stop()
        self.probe.process.terminate.assert_called_once_with()
        self.assertEqual(self.probe._arguments()[1:3], ["execution", "serve"])
        self.assertNotIn("--software-lock", self.probe._arguments())
