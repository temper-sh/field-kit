from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fieldkit_runtime.probe import ManagedProbe

from fieldkit_runtime.probe import (
    ProbeError,
    find_single_process,
    parse_process_rows,
    split_listen,
    validate_group_rows,
)


class ProbeBoundaryTest(unittest.TestCase):
    def test_process_rows_and_single_owned_process(self) -> None:
        rows = parse_process_rows(
            " 100 1 100 /tmp/llama-swap\n 101 100 100 /tmp/llama-server\n"
        )
        self.assertEqual(find_single_process(rows, name="llama-server", pgid=100)["pid"], 101)
        self.assertEqual(len(validate_group_rows(rows, 100)), 2)

    def test_duplicate_and_unexpected_processes_are_refused(self) -> None:
        duplicate = parse_process_rows("1 0 1 /a/llama-server\n2 0 1 /b/llama-server\n")
        with self.assertRaises(ProbeError):
            find_single_process(duplicate, name="llama-server", pgid=1)
        with self.assertRaises(ProbeError):
            validate_group_rows(parse_process_rows("1 0 1 /tmp/python\n"), 1)

    def test_listener_is_exact_loopback(self) -> None:
        self.assertEqual(split_listen("127.0.0.1:18080"), ("127.0.0.1", 18080))
        with self.assertRaises(ProbeError):
            split_listen("0.0.0.0:18080")

    def test_observation_failure_and_live_children_have_distinct_cleanup_meanings(self):
        class StoppedWatch:
            thread = SimpleNamespace(is_alive=lambda: False)
            def finish(self):
                raise ProbeError("process watcher fired a safety stop")
        with tempfile.TemporaryDirectory() as temporary:
            for children in ([], [{"pgid": 100}]):
                with self.subTest(children=children):
                    managed = ManagedProbe(temper=Path("/fixture/temper"), root=Path(temporary),
                        installation="fixture", software_lock=Path("/fixture/software.lock.yaml"),
                        generation="a" * 64, listen="127.0.0.1:18080", log_dir=Path(temporary),
                        watch_spec=json.loads((Path(__file__).resolve().parents[1] / "catalog/packages/qwen-machine-study@1/protocol.json").read_bytes())["process_watch"],
                        router_ready_seconds=30, log_bytes_max=1024)
                    managed.full_watch = StoppedWatch()
                    managed.full_binding = {"process_group_id": 100}
                    with patch("fieldkit_runtime.probe.listener_accepting", return_value=False), patch("fieldkit_runtime.probe.process_rows", return_value=children):
                        summary = managed.finish()
                    self.assertTrue(summary["issues"])
                    self.assertEqual(summary["safe_to_cleanup"], not children)


if __name__ == "__main__":
    unittest.main()
