from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

from fieldkit_runtime.catalog import Refusal
from fieldkit_runtime.watcher import (
    BINDING_SCHEMA,
    WATCH_SCHEMA,
    CoordinatedWatcher,
    WatchFailure,
    evaluate_snapshot,
    parse_footprint,
    parse_rss_bytes,
    parse_swap_used_bytes,
    parse_thermal,
    validate_binding,
    validate_watch_spec,
)


def watch_spec() -> dict:
    return {
        "schema": WATCH_SCHEMA,
        "interval_milliseconds": 5000,
        "max_gap_milliseconds": 15000,
        "command_timeout_seconds": 15,
        "roles": [
            {
                "id": "engine",
                "rss_bytes_max": 24 * 1024**3,
                "current_footprint_bytes_max": 24 * 1024**3,
                "peak_footprint_bytes_max": 24 * 1024**3,
            },
            {
                "id": "router",
                "rss_bytes_max": 2 * 1024**3,
                "current_footprint_bytes_max": 2 * 1024**3,
                "peak_footprint_bytes_max": 2 * 1024**3,
            },
        ],
        "swap_growth_bytes_max": 512 * 1024**2,
        "thermal_stop": True,
        "cpu_speed_limit_stop": True,
    }


def binding() -> dict:
    return {
        "schema": BINDING_SCHEMA,
        "process_group_id": 900,
        "roles": [
            {"id": "engine", "pid": 101, "pgid": 900, "ps_lstart": "engine-start"},
            {"id": "router", "pid": 102, "pgid": 900, "ps_lstart": "router-start"},
        ],
    }


def snapshot(*, engine_rss: int = 1, router_rss: int = 1) -> dict:
    return {
        "roles": {
            "engine": {
                "identity": {"pid": 101, "pgid": 900, "ps_lstart": "engine-start"},
                "current_footprint_bytes": 1,
                "peak_footprint_bytes": 1,
                "rss_bytes": engine_rss,
            },
            "router": {
                "identity": {"pid": 102, "pgid": 900, "ps_lstart": "router-start"},
                "current_footprint_bytes": 1,
                "peak_footprint_bytes": 1,
                "rss_bytes": router_rss,
            },
        },
        "swap_used_bytes": 1024,
        "thermal_warning": False,
        "cpu_speed_limit": 0,
    }


class ProcessWatcherTest(unittest.TestCase):
    def test_engine_may_have_its_own_bound_group(self) -> None:
        value = binding()
        value["roles"][0]["pgid"] = value["roles"][0]["pid"]
        self.assertEqual(validate_binding(watch_spec(), value), value)
        value["roles"][0]["pgid"] = 456
        with self.assertRaises(Refusal):
            validate_binding(watch_spec(), value)

    def test_watch_spec_requires_sorted_independent_roles(self) -> None:
        spec = watch_spec()
        validate_watch_spec(spec)
        spec["roles"].reverse()
        with self.assertRaisesRegex(Refusal, "sorted unique"):
            validate_watch_spec(spec)


    def test_role_limits_are_evaluated_without_summing_rss(self) -> None:
        gib = 1024**3
        peaks, reasons = evaluate_snapshot(
            watch_spec(),
            snapshot(engine_rss=23 * gib, router_rss=1 * gib),
            baseline_swap_bytes=1024,
            observed_rss_peaks={},
            gap_milliseconds=5000,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(peaks, {"engine": 23 * gib, "router": 1 * gib})

        _, reasons = evaluate_snapshot(
            watch_spec(),
            snapshot(engine_rss=1 * gib, router_rss=2 * gib),
            baseline_swap_bytes=1024,
            observed_rss_peaks=peaks,
            gap_milliseconds=5000,
        )
        self.assertEqual(
            [(item["kind"], item.get("role")) for item in reasons],
            [("rss", "router")],
        )

    def test_system_and_watcher_gap_stops_remain_independent(self) -> None:
        value = snapshot()
        value["swap_used_bytes"] += 512 * 1024**2
        value["thermal_warning"] = True
        value["cpu_speed_limit"] = 1
        _, reasons = evaluate_snapshot(
            watch_spec(),
            value,
            baseline_swap_bytes=1024,
            observed_rss_peaks={},
            gap_milliseconds=15001,
            sleep_wake_milliseconds=15001,
        )
        self.assertEqual(
            [item["kind"] for item in reasons],
            [
                "swap-growth",
                "thermal-warning",
                "cpu-speed-limit",
                "watcher-gap",
                "sleep-wake",
            ],
        )

    def test_macos_counter_parsers_preserve_raw_units(self) -> None:
        self.assertEqual(
            parse_footprint(
                "phys_footprint: 1,622,352 B\nphys_footprint_peak: 1,638,736 B\n"
            ),
            (1622352, 1638736),
        )
        self.assertEqual(parse_rss_bytes(" 17833424\n"), 17833424 * 1024)
        self.assertEqual(
            parse_swap_used_bytes("total = 2048.00M used = 501.75M free = 1546.25M"),
            501 * 1024**2 + (75 * 1024**2) // 100,
        )
        self.assertEqual(
            parse_thermal("No thermal warning level has been recorded\nCPU Speed Limit: 0"),
            (False, 0),
        )
        self.assertEqual(
            parse_thermal(
                "Note: No thermal warning level has been recorded\n"
                "Note: No performance warning level has been recorded\n"
                "Note: No CPU power status has been recorded\n"
            ),
            (False, 0),
        )
        with self.assertRaises(WatchFailure):
            parse_footprint("phys_footprint: 100 B\n")



    def test_coordinated_watcher_retains_both_roles_and_stop_decision(self) -> None:
        spec = watch_spec()
        stopped = snapshot(router_rss=2 * 1024**3)
        terminations: list[tuple[dict, float]] = []

        def request_stop():
            terminations.append(True)

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "watch.jsonl"
            watcher = CoordinatedWatcher(
                spec,
                binding(),
                output,
                sampler=lambda _binding, _timeout: stopped,
                request_stop=request_stop,
                monotonic_ns=lambda: 1_000_000,
            )
            result = watcher.run(Event())
            records = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["stop_reasons"][0]["role"], "router")
        self.assertEqual(terminations, [True])
        self.assertEqual(sorted(records[1]["snapshot"]["roles"]), ["engine", "router"])

    def test_coordinated_watcher_uses_the_prestart_swap_baseline(self) -> None:
        spec = watch_spec()
        spec["swap_growth_bytes_max"] = 50
        terminations = []
        value = snapshot()
        value["swap_used_bytes"] = 1050

        with tempfile.TemporaryDirectory() as temporary:
            watcher = CoordinatedWatcher(
                spec,
                binding(),
                Path(temporary) / "watch.jsonl",
                baseline_swap_bytes=1000,
                sampler=lambda _binding, _timeout: value,
                request_stop=lambda: terminations.append(True),
            )
            result = watcher.run(Event())

        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["stop_reasons"][0]["kind"], "swap-growth")
        self.assertEqual(terminations, [True])


if __name__ == "__main__":
    unittest.main()
