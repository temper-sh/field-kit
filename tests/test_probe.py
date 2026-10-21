import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fieldkit_runtime.probe import ManagedProbe, ProbeError, split_listen, summarize_watch
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

    def test_partial_startup_keeps_router_watch_until_all_helper_roles_are_bound(self):
        self.status.update(state="running", listeners_verified=True, safe_to_cleanup=False)
        self.probe.watch_spec["roles"].insert(1, {**self.probe.watch_spec["roles"][1], "id": "frontend"})
        complete_roles = [self.status["roles"][0],
                          {"id": "frontend", "pid": 103, "pgid": 900, "ps_lstart": "frontend-start"},
                          self.status["roles"][1]]
        router_watch = Mock()
        self.probe.router_watch = router_watch
        # The same ordering applies to MLX: an engine can be published before
        # its remaining helper roles. A missing role is not yet a bad identity.
        self.status["roles"] = [role for role in complete_roles if role["id"] != "frontend"]
        self.publish()
        self.assertFalse(self.probe.observe_engine())
        self.assertIsNone(self.probe.full_watch)
        router_watch.finish.assert_not_called()
        self.status["roles"] = complete_roles
        self.publish()
        with patch("fieldkit_runtime.probe._WatchHandle"), patch("fieldkit_runtime.probe.watcher.CoordinatedWatcher"):
            self.assertTrue(self.probe.observe_engine())
        self.assertIsNone(self.probe.router_watch)
        self.assertEqual(self.probe.full_binding, {**binding(), "roles": complete_roles})
        self.status["roles"][0]["ps_lstart"] = "rebound helper"
        self.publish()
        with self.assertRaises(ProbeError):
            self.probe.validate_owned_boundary()

    def test_go_status_fractional_seconds_are_accepted(self):
        # Go's RFC3339Nano omits trailing zeros, producing any precision 0–9.
        second = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for precision in range(10):
            fraction = "." + "123456789"[:precision] if precision else ""
            for zone in ("Z", "+00:00"):
                with self.subTest(precision=precision, zone=zone):
                    self.status["updated_at"] = second + fraction + zone
                    self.publish()
                    self.assertEqual(self.probe._status(), self.status)

    def test_invalid_status_timestamps_are_refused(self):
        for timestamp in (
            None, 123, [], "", "2026-09-24T00:00:00",
            "2026-09-24T00:00:00.Z", "2026-09-24T00:00:00.1234567890Z",
            "2026-09-24T00:00:00+24:00", "2026-09-24T00:00:00+00:60",
            "2026-02-30T00:00:00Z",
            "2026-09-24T00:00:00Zjunk",
        ):
            with self.subTest(timestamp=timestamp):
                self.status["updated_at"] = timestamp
                self.publish()
                with self.assertRaisesRegex(ProbeError, "invalid probe status"):
                    self.probe._status(final=True)

    def test_future_nanosecond_status_is_still_refused(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
        self.status["updated_at"] = future.strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"
        self.publish()
        with self.assertRaisesRegex(ProbeError, "stale"):
            self.probe._status()

    def test_stop_only_signals_its_temper_child(self):
        self.probe.process.poll.return_value = None
        self.probe.request_stop()
        self.probe.process.terminate.assert_called_once_with()
        self.assertEqual(self.probe._arguments()[1:3], ["execution", "serve"])
        self.assertNotIn("--software-lock", self.probe._arguments())


class SummaryTest(unittest.TestCase):
    def test_wired_growth_is_measured_from_the_baseline_before_the_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "process-watch.jsonl"
            samples = [{"event": "sample", "snapshot": {"swap_used_bytes": 5, "wired_bytes": wired, "roles": {}}}
                       for wired in (900, 1300, 1100)]
            path.write_text("".join(json.dumps(sample) + "\n" for sample in samples))
            summary = summarize_watch(path, 5, 1000)
            self.assertEqual((summary["wired_peak_bytes"], summary["wired_growth_bytes"]), (1300, 300))
            self.assertIsNone(summarize_watch(path, 5)["wired_growth_bytes"])
