"""The basic check: one catalog preset, the Flask coding task, one standard record.

Fixed method, no AI. Temper compiles, installs and serves the preset; Field
Kit measures each case once, keeps every first attempt and cleans up only
after Temper confirms that its processes stopped. A tuning trial runs a
Temper fork of a basic-check preset through the same steps and records its
changes, so it stays separate from the preset's baseline. A trial may change
settings, weights or the template; Temper plans its downloads.
"""
from __future__ import annotations

import datetime
import difflib
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from . import watcher
from .coding.evaluation import unpack
from .coding.patches import InvalidPatch, staged_texts, submission
from .common import Refusal, canonical_json, digest, load_json, read_regular
from .probe import ManagedProbe, ProbeError
from .process import atomic_write, exclusive_lock, file_digest, runtime_identity
from .serving import HTTPResponseError, monitored_call
from .splash_memory import SplashMemory
from .stream import CodingStream
from .temper import Temper, locate

PROTOCOL = Path("protocols/basic-check.json")
RUN_SCHEMA = "field-kit-basic-check-run/v1"
RESULT_SCHEMA = "field-kit-basic-check-result/v1"
INSTALLATION = "basic-check"
GIB = 1024**3
MIB = 1024**2
UNFINISHED = ("preparing", "measuring", "cleaning")
PRESET_FILE_BYTES_MAX = 64 * 1024
TEMPLATE_FILE_BYTES_MAX = 1 * MIB


# ---------------------------------------------------------------- protocol

def load_protocol(repository: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """The protocol, its cases in protocol order, and the protocol's digest."""
    path = repository / PROTOCOL
    raw = read_regular(path)
    protocol = load_json(path)
    if protocol.get("schema") != "field-kit-protocol/v1" or protocol.get("id") != "basic-check":
        raise Refusal("protocols/basic-check.json is not the basic-check protocol")
    task = protocol["task"]
    folder = repository / task["path"]
    cases_raw = read_regular(folder / "cases.json", 4 * MIB)
    if digest(cases_raw) != task["cases_sha256"]:
        raise Refusal("the Flask cases differ from the protocol; restore this checkout")
    if file_digest(folder / "flask.tar.gz") != task["fixture_sha256"]:
        raise Refusal("the Flask fixture differs from the protocol; restore this checkout")
    by_id = {case["id"]: case for case in json.loads(cases_raw)["cases"]}
    cases = [by_id[identity] for identity in task["cases"]]
    return protocol, cases, digest(raw)


# ---------------------------------------------------------- pure decisions

def engine_family(catalog: dict[str, Any], preset: str) -> str:
    engine = catalog["engines"][catalog["presets"][preset]["engine"]]
    return engine["adapter"].split("/", 1)[0]


def eligible_presets(catalog: dict[str, Any], protocol: dict[str, Any]) -> list[str]:
    """General chat presets, in the protocol's order, then any new ones by ID."""
    wanted = protocol["presets"]
    found = [preset for preset, record in catalog["presets"].items()
             if record.get("interface") == wanted["interface"]
             and (record.get("presentation") or {}).get("group") == wanted["group"]]
    order = [preset for preset in wanted["default_order"] if preset in found]
    return order + sorted(set(found) - set(order))


def engine_budget(facts: dict[str, Any], fraction: float) -> int:
    return min(int(facts["physical_memory_bytes"] * fraction), facts["wired_limit_mib"] * MIB)


def admission(plan: dict[str, Any], preset: str, facts: dict[str, Any], protocol: dict[str, Any]) -> str | None:
    """Why this preset cannot run here, or None. A floor, not a fit prediction."""
    if not plan["can_prepare"]:
        reasons = plan.get("refusals") or plan["presets"][preset].get("refusals") or ["Temper cannot install it here"]
        return "; ".join(reasons)
    rule = protocol["admission"]
    need = plan["presets"][preset]["model_bytes"] + rule["headroom_bytes"]
    budget = engine_budget(facts, rule["engine_memory_fraction"])
    if need > budget:
        return f"needs about {need / GIB:.1f} GiB for the model and headroom; this Mac allows {budget / GIB:.1f} GiB"
    return None


def choose(survey: list[dict[str, Any]], requested: str | None) -> dict[str, Any]:
    if requested:
        row = next((row for row in survey if row["preset"] == requested), None)
        if row is None:
            names = ", ".join(row["preset"] for row in survey)
            raise Refusal(f"the basic check covers these presets: {names}")
        if row["blocked"]:
            raise Refusal(f"{requested} cannot run on this Mac: {row['blocked']}")
        return row
    for row in survey:
        if not row["blocked"]:
            return row
    raise Refusal("no preset of the basic check can run on this Mac; see the reasons above")


def preset_file(path: Path) -> tuple[str, dict[str, bytes]]:
    """A user preset's ID, which is its file name, and the files that go into presets.d:
    the preset and any local template files next to it. Temper checks the content."""
    if path.suffix != ".json" or not path.stem:
        raise Refusal(f"a Temper preset file is named ID.json: {path}")
    data = read_regular(path, PRESET_FILE_BYTES_MAX)
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refusal(f"invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != "temper-preset/v1":
        raise Refusal(f"{path} is not a Temper preset file (temper-preset/v1)")
    files = {path.name: data}
    for record in (document.get("templates") or {}).values():
        if not isinstance(record, dict) or not any(isinstance(source, dict) and source.get("hub") == "local"
                                                   for source in record.get("sources") or []):
            continue
        for entry in record.get("files") or []:
            name = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name or "/" in name or name.startswith(".") or name.endswith(".json"):
                raise Refusal(f"{path} names a local template file that is not a plain file name: {name!r}")
            files[name] = read_regular(path.parent / name, TEMPLATE_FILE_BYTES_MAX)
    return path.stem, files


def trial_origin(checked: dict[str, Any], survey: list[dict[str, Any]]) -> dict[str, Any]:
    """The basic-check preset that a trial forks. A copy, which can change the engine, is
    refused: Field Kit has measured only the catalog's engines through the basic check."""
    origin = checked.get("origin")
    if checked.get("form") != "fork":
        raise Refusal("a trial is a fork of a basic-check preset (temper preset fork); copies, which can"
                      " change the engine, are not supported yet")
    row = next((row for row in survey if row["preset"] == origin), None)
    if row is None:
        names = ", ".join(entry["preset"] for entry in survey)
        raise Refusal(f"a trial forks one of these presets: {names}")
    if not checked["changes"]:
        raise Refusal(f"the preset file changes nothing; run ./field-kit check --preset {origin} instead")
    return row


def context_window(record: dict[str, Any], profile_min_memory_gib: Any) -> int | None:
    """The window of the catalog profile that Temper compiled."""
    for profile in record.get("profiles") or []:
        if profile.get("min_memory_gib") == profile_min_memory_gib:
            return profile.get("context_window_tokens")
    return None


TEMPLATE_BYTES_MAX = 1 * MIB


def cache_mode(shared_plan: dict[str, Any]) -> str:
    """Reuse the user's Hugging Face cache only when it already holds every model file.

    Then at most a few small chat template files are added to it; anything
    larger goes to a private cache that the run removes afterwards.
    """
    remaining = shared_plan.get("remaining_downloads") or []
    models = [item for item in remaining if item.get("kind") == "model"]
    templates = sum(item.get("bytes", 0) for item in remaining if item.get("kind") == "template")
    return "private" if models or templates > TEMPLATE_BYTES_MAX else "shared"


def watch_spec(protocol: dict[str, Any], facts: dict[str, Any], family: str) -> dict[str, Any]:
    limits = protocol["process_watch"]
    engine = int(facts["physical_memory_bytes"] * limits["engine_memory_fraction"])
    helper = limits["helper_bytes_max"]
    roles = [{"id": "engine", "limit": engine}, {"id": "router", "limit": helper}]
    if family == "splash":
        roles.append({"id": "frontend", "limit": helper})
    return {
        "schema": watcher.WATCH_SCHEMA,
        "interval_milliseconds": limits["interval_milliseconds"],
        "max_gap_milliseconds": limits["max_gap_milliseconds"],
        "command_timeout_seconds": limits["command_timeout_seconds"],
        "roles": sorted(({"id": role["id"], "rss_bytes_max": role["limit"], "current_footprint_bytes_max": role["limit"],
                          "peak_footprint_bytes_max": role["limit"]} for role in roles), key=lambda role: role["id"]),
        "swap_growth_bytes_max": limits["swap_growth_bytes_max"],
        "thermal_stop": limits["thermal_stop"],
        "cpu_speed_limit_stop": limits["cpu_speed_limit_stop"],
    }


def classify_failure(error: BaseException) -> str:
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    message = str(error).lower()
    if isinstance(error, HTTPResponseError) and "context" in message:
        return "exceeds-context"
    if any(text in message for text in ("out of memory", "out-of-memory", "insufficient memory", "allocation failed")):
        return "memory-failure"
    if "safety stop" in message:
        return "stopped-by-watch"
    return "runtime-failure"


def rates(response: dict[str, Any]) -> dict[str, Any]:
    """Native engine rates when the engine reports them; never estimated."""
    timings = response.get("timings") or {}

    def rate(tokens: str, milliseconds: str) -> float | None:
        count, elapsed = timings.get(tokens), timings.get(milliseconds)
        if type(count) is int and count > 0 and type(elapsed) in (int, float) and elapsed > 0:
            return count * 1000 / elapsed
        return None

    usage = response.get("usage") or {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "prefill_tokens_per_second": rate("prompt_n", "prompt_ms"),
        "generation_tokens_per_second": rate("predicted_n", "predicted_ms"),
    }


def resources_valid(resources: dict[str, Any]) -> bool:
    return bool(resources.get("samples")) and not resources.get("issues") and not resources.get("stop_reasons") \
        and not resources.get("thermal_warning_observed") and not resources.get("cpu_speed_limit_max") \
        and resources.get("max_gap_milliseconds", 0) <= 15000 and resources.get("max_sleep_wake_milliseconds", 0) <= 15000


def patch_check(response: dict[str, Any], base: Path, scope: dict[str, Any]) -> dict[str, Any]:
    """Check the submission against the task's edit scope; run nothing."""
    arguments = submission(response)
    texts, originals = staged_texts(arguments, base, scope)
    diff = "".join("".join(difflib.unified_diff(
        originals[name].splitlines(keepends=True), value.splitlines(keepends=True),
        fromfile="a/" + name, tofile="b/" + name)) for name, value in sorted(texts.items()))
    return {"changed_files": sorted(texts), "diff": diff}


def case_status(row: dict[str, Any]) -> str:
    if row.get("failure"):
        return row["failure"]["kind"]
    if row.get("finish_reason") == "length":
        return "context-window-exhausted"
    if row.get("submission_error"):
        return "invalid-submission"
    if row.get("patch"):
        return "patch-submitted"
    return "incomplete"


def keep_going(row: dict[str, Any]) -> bool:
    """A model-side result lets the next case run; a machine-side stop does not."""
    return row["status"] in ("patch-submitted", "invalid-submission", "context-window-exhausted", "exceeds-context") \
        and row.get("safe_to_cleanup", False)


# ----------------------------------------------------------------- display

def gib(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value / GIB:.1f} GiB" if value >= GIB else f"{value / MIB:.0f} MiB"


def number(value: float | int | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def setting(value: Any) -> str:
    if type(value) is int:
        return f"{value:,}"
    return value if isinstance(value, str) else json.dumps(value)


def describe_changes(changes: list[dict[str, Any]]) -> str:
    return "; ".join(f"{change['path']} {setting(change.get('from'))} → {setting(change.get('to'))}" for change in changes)


def describe_survey(survey: list[dict[str, Any]], chosen: str | None) -> list[str]:
    lines = []
    for row in survey:
        mark = "→" if row["preset"] == chosen else " "
        state = row["blocked"] or f"can run · model {gib(row['model_bytes'])}"
        lines.append(f" {mark} {row['preset']:<38} {state}")
    return lines


def consent_text(run: dict[str, Any], protocol: dict[str, Any]) -> str:
    plan, preset, limits = run["plan"], run["preset"], protocol["limits"]
    cache = ("your Hugging Face cache already has the model; at most its small chat template is added there"
             if run["cache"]["mode"] == "shared" else
             "a private model cache inside this run, removed afterwards")
    downloads = plan["remaining_download_bytes"]
    trial = run.get("trial")
    title = ([f"Tuning trial {preset['name']} ({preset['id']}, {preset['family']}), a fork of {trial['origin']}",
              f"Changes: {describe_changes(trial['changes'])}"] if trial else
             [f"Basic check of {preset['name']} ({preset['id']}, {preset['family']})"])
    return "\n".join([
        *title,
        f"Mac: {run['machine'].get('chip', 'Apple Silicon')}, {gib(run['machine']['physical_memory_bytes'])} memory",
        f"Catalog sequence {run['catalog']['sequence']}; Temper {run['temper']['version'].removeprefix('temper ')}",
        "",
        f"Downloads: {gib(downloads)} (model files: {cache})",
        f"Disk needed: {gib(plan['remaining_disk_bytes'])} of {gib(plan['free_disk_bytes'])} free",
        f"Work: {len(protocol['task']['cases'])} Flask coding cases, each one request on a fresh engine",
        f"Time limits: install {duration(limits['prepare_seconds'])}, start {duration(limits['startup_seconds'])},"
        f" each case {duration(limits['request_seconds'])}",
        "Stops early on memory pressure (swap growth), heat or CPU speed limits.",
        "Afterwards Field Kit removes the engine, the run's Temper folder and any private model cache.",
        f"Results stay in runs/{run['id']}/; nothing is uploaded.",
    ])


def room_lines(run: dict[str, Any], protocol: dict[str, Any]) -> list[str]:
    """How far the run stayed from this model's and this Mac's limits."""
    preset, lines = run["preset"], []
    window, limit = preset.get("context_window_tokens"), preset.get("context_limit_tokens")
    if window and limit:
        lines.append(f"- Context window: {window:,} tokens; the model accepts up to {limit:,}.")
    for row in run["cases"]:
        if row["status"] == "context-window-exhausted":
            lines.append(f"- {row['id']} filled the window: {number(row.get('input_tokens'), 0)} input and"
                         f" {number(row.get('output_tokens'), 0)} output tokens.")
    peaks = [value for value in ((row.get("memory") or {}).get("engine_peak_wired_bytes") for row in run["cases"]) if value]
    if peaks:
        budget = engine_budget(run["machine"], protocol["admission"]["engine_memory_fraction"])
        lines.append(f"- Engine peak {gib(max(peaks))} of the {gib(budget)} an engine may use on this Mac"
                     f" (three quarters of its memory, within the GPU limit): {gib(max(0, budget - max(peaks)))} left.")
    return lines


def render_report(run: dict[str, Any], protocol: dict[str, Any]) -> bytes:
    preset, trial = run["preset"], run.get("trial")
    lines = [f"# {'Tuning trial' if trial else 'Basic check'} — {preset['name']}", "",
             *([f"- Fork of `{trial['origin']}`: {describe_changes(trial['changes'])}"] if trial else []),
             f"- Preset: `{preset['id']}` ({preset['family']}), catalog sequence {run['catalog']['sequence']}",
             f"- Mac: {run['machine'].get('chip', 'Apple Silicon')}, {gib(run['machine']['physical_memory_bytes'])},"
             f" macOS {run['machine'].get('target', {}).get('distribution_version', '?')}",
             f"- Temper {run['temper']['version'].removeprefix('temper ')}, Field Kit {run['field_kit']['version']},"
             f" protocol basic-check@{run['protocol']['revision']}",
             f"- State: {run['state']}" + (f" ({run['stopped']})" if run.get("stopped") else ""), "",
             "| Case | Result | Input tokens | Output tokens | Prefill tok/s | Generation tok/s | Task time | Start time | Engine after load | Engine peak |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in run["cases"]:
        memory = row.get("memory") or {}
        lines.append("| {id} | {status}{valid} | {i} | {o} | {p} | {g} | {t} | {s} | {l} | {k} |".format(
            id=row["id"], status=row["status"], valid="" if row.get("measurement_valid") else " (not comparable)",
            i=number(row.get("input_tokens"), 0), o=number(row.get("output_tokens"), 0),
            p=number(row.get("prefill_tokens_per_second")), g=number(row.get("generation_tokens_per_second")),
            t=duration(row.get("task_seconds")), s=duration(row.get("startup_seconds")),
            l=gib(memory.get("engine_loaded_wired_bytes")), k=gib(memory.get("engine_peak_wired_bytes"))))
    native = [row for row in run["cases"] if (row.get("memory") or {}).get("splash_native")]
    if native:
        lines += ["", "Splash native Metal memory (overlaps the process numbers; do not add them):", ""]
        for row in native:
            record = row["memory"]["splash_native"]
            loaded = record.get("loaded") or {}
            last = record.get("last") or {}
            lines.append(f"- {row['id']}: after load {gib(loaded.get('current_bytes'))},"
                         f" peak {gib(last.get('peak_bytes'))} ({record.get('state')})")
    lines += ["", "## Room to tune", "", *room_lines(run, protocol), "",
              "Field Kit looks for the best setup each Mac can use: the largest context window and the fastest"
              " speeds that still finish the task, without swap growth or heat stops. The catalog's settings are"
              " a starting point. Try a change as a tuning trial: see \"Tuning trials\" in"
              " docs/experiments/basic-check.md."]
    if trial:
        lines.append(f"Compare this trial with a basic check of `{trial['origin']}` on the same Mac.")
    lines += ["",
              "Results: **patch-submitted** means the model returned a patch inside the task's edit scope;"
              " its tests run later, at review. **not comparable** means the speed or memory numbers were"
              " disturbed (heat, sleep, missing samples) or the stream did not finish.",
              "Rates are the engine's own counters. Engine memory is how much the Mac's wired memory grew"
              " from before the engine started: after the model loaded, and the highest sample during the"
              " case. It includes the model as the GPU holds it; other apps that wire memory add to it.",
              "", "Send `result.json` to the coordinator if you want to share the run. It contains the patches"
              " the model submitted, your Mac's details and local paths."]
    return ("\n".join(lines) + "\n").encode()


def result_document(run: dict[str, Any]) -> dict[str, Any]:
    return {"schema": RESULT_SCHEMA, **{key: run[key] for key in (
        "id", "protocol", "field_kit", "temper", "machine", "catalog", "preset", "lock_sha256", "cache",
        "plan", "state", "stopped", "preparation", "cases", "cleanup")}, "trial": run.get("trial")}


# ----------------------------------------------------------------- effects

def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_listen() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{probe_socket.getsockname()[1]}"


def private_environment(cache: Path) -> dict[str, str]:
    return {**os.environ, "HF_HUB_CACHE": str(cache)}


class BasicCheck:
    def __init__(self, repository: Path, temper: Temper, *, ask: Callable[[str], str] = input,
                 say: Callable[[str], None] = print):
        self.repository = repository
        self.temper = temper
        self.ask, self.say = ask, say
        self.protocol, self.cases, self.protocol_sha256 = load_protocol(repository)
        self.runs = repository / "runs"

    # -- discovery

    def unfinished(self) -> list[Path]:
        if not self.runs.is_dir():
            return []
        found = []
        for path in sorted(self.runs.glob("*/run.json")):
            try:
                state = load_json(path).get("state")
            except Refusal:
                continue
            if state in UNFINISHED or state == "retained":
                found.append(path.parent)
        return found

    def survey(self, root: Path, facts: dict[str, Any], environment: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self.temper.catalog_update(root)
        inspected = self.temper.catalog(root)
        catalog = inspected["catalog"]
        rows = []
        for preset in eligible_presets(catalog, self.protocol):
            plan = self.temper.install_plan(root, preset, environment)
            record = catalog["presets"][preset]
            rows.append({"preset": preset, "name": (record.get("presentation") or {}).get("display_name", preset),
                         "engine": record["engine"], "family": engine_family(catalog, preset),
                         "model_bytes": plan["presets"][preset]["model_bytes"],
                         "blocked": admission(plan, preset, facts, self.protocol), "plan": plan})
        return inspected, rows

    def trial(self, root: Path, source: tuple[str, dict[str, bytes]], rows: list[dict[str, Any]],
              facts: dict[str, Any], environment: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Temper's check and install plan of the user's preset in this Temper folder, and the row it forks."""
        preset, files = source
        for name, data in files.items():
            atomic_write(root / "presets.d" / name, data)
        checked = self.temper.preset_check(root, preset, facts["physical_memory_bytes"] // GIB)
        row = trial_origin(checked, rows)
        plan = self.temper.preset_plan(root, preset, environment)
        blocked = admission(plan, preset, facts, self.protocol)
        if blocked:
            raise Refusal(f"{preset} cannot run on this Mac: {blocked}")
        return checked, row, plan

    # -- commands

    def preview(self, requested: str | None, preset_path: Path | None = None) -> int:
        source = preset_file(preset_path) if preset_path else None
        identity = self.temper.identity()
        facts = self.temper.machine_facts()
        root = self.repository / ".local" / "preview"
        root.mkdir(parents=True, exist_ok=True)
        inspected, rows = self.survey(root, facts, dict(os.environ))
        self.say(f"Temper {identity['version'].removeprefix('temper ')} · catalog sequence {inspected['active']['sequence']}"
                 f" · {facts.get('chip', 'Apple Silicon')} with {gib(facts['physical_memory_bytes'])}")
        self.say("Presets of the basic check on this Mac:")
        checked = None
        try:
            if source:
                checked, chosen, plan = self.trial(root, source, rows, facts, dict(os.environ))
            else:
                chosen = choose(rows, requested)
                plan = chosen["plan"]
        except Refusal as error:
            for line in describe_survey(rows, None):
                self.say(line)
            raise error
        finally:
            for name in (source[1] if source else ()):
                (root / "presets.d" / name).unlink(missing_ok=True)
        for line in describe_survey(rows, chosen["preset"]):
            self.say(line)
        mode = cache_mode(plan)
        downloads = plan["remaining_download_bytes"] if mode == "shared" else plan["download_bytes"]
        self.say("")
        if checked:
            self.say(f"Tuning trial {source[0]}, a fork of {chosen['preset']}: {describe_changes(checked['changes'])}")
            for warning in checked.get("warnings") or []:
                self.say(f"Temper: {warning}")
        self.say(f"The {'trial' if checked else 'check'} would install {source[0] if checked else chosen['preset']}"
                 f" and download about {gib(downloads)}"
                 + (" (the model is already in your Hugging Face cache)." if mode == "shared" else "."))
        self.say("Preview only: nothing was downloaded and no run was created. Run ./field-kit check to start.")
        return 0

    def start(self, requested: str | None, *, preset_path: Path | None = None, assume_yes: bool = False,
              plan_only: bool = False) -> int:
        pending = self.unfinished()
        if pending and plan_only:
            raise Refusal(f"run {pending[0].name} is unfinished; ./field-kit check continues it before another can start")
        if pending:
            return self.resume(pending[0])
        source = preset_file(preset_path) if preset_path else None
        identity = self.temper.identity()
        facts = self.temper.machine_facts()
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = self.runs / run_id
        directory.mkdir(parents=True, exist_ok=False)
        (directory / ".lock").touch()
        accepted = False
        try:
            root = directory / "temper"
            inspected, rows = self.survey(root, facts, dict(os.environ))
            self.say("Presets of the basic check on this Mac:")
            chosen = checked = trial_plan = None
            try:
                if source:
                    checked, chosen, trial_plan = self.trial(root, source, rows, facts, dict(os.environ))
                else:
                    chosen = choose(rows, requested)
            finally:
                for line in describe_survey(rows, chosen["preset"] if chosen else None):
                    self.say(line)
            preset = source[0] if source else chosen["preset"]
            lock = directory / "execution.lock.json"
            compiled = self.temper.compile(root, preset, facts["physical_memory_bytes"] // GIB, lock)
            if source and compiled.get("user_preset") is not True:
                raise Refusal(f"Temper compiled a catalog preset instead of the preset file {source[0]}.json")
            execution = self.temper.inspect(lock)
            plan = trial_plan or chosen["plan"]
            mode = cache_mode(plan)
            cache = directory / "hf-cache"
            if mode == "private":
                cache.mkdir()
                plan = (self.temper.preset_plan(root, preset, private_environment(cache)) if source else
                        self.temper.install_plan(root, preset, private_environment(cache)))
                if not plan["can_prepare"]:
                    raise Refusal("Temper cannot install the preset into a private cache: "
                                  + "; ".join(plan.get("refusals") or ["no reason given"]))
            record = inspected["catalog"]["presets"][chosen["preset"]]
            run = {
                "schema": RUN_SCHEMA, "id": run_id, "state": "planned",
                "protocol": {"id": "basic-check", "revision": self.protocol["revision"], "sha256": self.protocol_sha256},
                "field_kit": runtime_identity(), "temper": identity, "machine": facts,
                "catalog": inspected["active"],
                "preset": {"id": preset, "name": checked["name"] if checked else chosen["name"], "engine": chosen["engine"],
                           "family": chosen["family"], "profile_min_memory_gib": compiled.get("profile_min_memory_gib"),
                           "context_window_tokens": checked.get("context_window_tokens") if checked else
                           context_window(record, compiled.get("profile_min_memory_gib")),
                           "context_limit_tokens": record.get("context_limit_tokens")},
                "trial": None if not checked else {
                    "origin": chosen["preset"], "changes": checked["changes"], "warnings": checked.get("warnings") or [],
                    "file": json.loads(source[1][Path(preset_path).name]),
                    "files_sha256": {name: digest(data) for name, data in sorted(source[1].items())}},
                "execution": execution, "lock_sha256": file_digest(lock),
                "cache": {"mode": mode, "path": str(cache) if mode == "private" else None},
                "plan": {key: plan[key] for key in ("download_bytes", "remaining_download_bytes", "fresh_disk_bytes",
                                                    "remaining_disk_bytes", "free_disk_bytes")},
                "approved_at": None, "preparation": None, "cases": [], "stopped": None,
                "safe_to_cleanup": True, "cleanup": "pending",
            }
            self.say("")
            self.say(consent_text(run, self.protocol))
            if plan_only:
                self.say("\nPlan only: nothing was downloaded and no run was created.")
                return 0
            answer = "yes" if assume_yes else self.ask("\nType yes to start, anything else to cancel: ").strip().lower()
            if answer != "yes":
                self.say("Cancelled. Nothing was downloaded.")
                return 0
            run.update(state="preparing", approved_at=utc_now())
            self.save(directory, run)
            accepted = True
        finally:
            if not accepted:
                shutil.rmtree(directory, ignore_errors=True)
        return self.continue_run(directory)

    def resume(self, directory: Path) -> int:
        run = load_json(directory / "run.json", 64 * MIB)
        if run["state"] == "retained":
            raise Refusal(f"run {run['id']} kept its installation because Temper could not confirm that its processes"
                          f" stopped. Keep runs/{run['id']}/ and send the maintainer its report.md and the error above.")
        self.say(f"Continuing unfinished run {run['id']} ({run['preset']['id']}).")
        return self.continue_run(directory)

    def continue_run(self, directory: Path) -> int:
        with exclusive_lock(directory / ".lock"):
            run = load_json(directory / "run.json", 64 * MIB)
            self.verify_identity(directory, run)
            previous = signal.signal(signal.SIGTERM, _interrupt)
            try:
                if run["state"] == "preparing":
                    self.prepare(directory, run)
                if run["state"] == "measuring":
                    self.measure(directory, run)
                if run["state"] == "cleaning":
                    self.cleanup(directory, run)
            finally:
                signal.signal(signal.SIGTERM, previous)
                self.save(directory, run)
                atomic_write(directory / "result.json", canonical_json(result_document(run)))
                atomic_write(directory / "report.md", render_report(run, self.protocol))
        self.say("")
        self.say(render_report(run, self.protocol).decode())
        self.say(f"Report: {directory / 'report.md'}")
        self.say(f"Result: {directory / 'result.json'}")
        if run["state"] in UNFINISHED:
            self.say("The run stopped before it finished. Run ./field-kit check to continue it.")
            return 1
        return 0 if run["state"] == "complete" else 1

    def verify_identity(self, directory: Path, run: dict[str, Any]) -> None:
        identity = self.temper.identity()
        if identity["sha256"] != run["temper"]["sha256"]:
            started = run["temper"]
            raise Refusal(f"Temper changed since this run started. A run finishes with the Temper it started with:"
                          f" {started['version']} at {started['path']}. If that file still exists, continue with"
                          f" ./field-kit check --temper {started['path']}")
        if runtime_identity()["sha256"] != run["field_kit"]["sha256"] or self.protocol_sha256 != run["protocol"]["sha256"]:
            raise Refusal("this checkout changed since the run started; restore it to finish the run")
        if file_digest(directory / "execution.lock.json") != run["lock_sha256"]:
            raise Refusal("the run's execution lock changed")

    def save(self, directory: Path, run: dict[str, Any]) -> None:
        atomic_write(directory / "run.json", canonical_json(run))

    def environment(self, run: dict[str, Any]) -> dict[str, str]:
        if run["cache"]["mode"] == "private":
            return private_environment(Path(run["cache"]["path"]))
        return dict(os.environ)

    def prepare(self, directory: Path, run: dict[str, Any]) -> None:
        self.say(f"Installing {run['preset']['id']}. Model downloads can take a long time.")
        started = time.monotonic()
        material = self.temper.prepare(directory / "execution.lock.json", directory / "temper", INSTALLATION,
                                     self.protocol["limits"]["prepare_seconds"], self.environment(run))
        if material.get("execution") != run["execution"]:
            raise Refusal("Temper prepared a different execution")
        run["preparation"] = {"generation": material["generation"], "seconds": time.monotonic() - started}
        fixture = directory / "fixture"
        if fixture.exists():
            shutil.rmtree(fixture)
        unpack(self.repository / self.protocol["task"]["path"] / "flask.tar.gz", fixture)
        run["state"] = "measuring"
        self.save(directory, run)

    def measure(self, directory: Path, run: dict[str, Any]) -> None:
        run["stopped"] = None
        done = {row["id"] for row in run["cases"]}
        for case in self.cases:
            if case["id"] in done:
                continue
            self.say(f"Measuring {case['id']} ...")
            row = self.measure_case(directory, run, case)
            run["cases"].append(row)
            run["safe_to_cleanup"] = run["safe_to_cleanup"] and row["safe_to_cleanup"]
            self.save(directory, run)
            self.say(f"  {row['status']}, {duration(row.get('task_seconds'))}")
            if not keep_going(row):
                run["stopped"] = f"{case['id']}: {row['status']}"
                if row["status"] == "interrupted" and row["safe_to_cleanup"]:
                    return  # resumable; the interrupted first attempt stays recorded
                break
        run["state"] = "cleaning" if run["safe_to_cleanup"] else "retained"
        run["cleanup"] = "pending" if run["safe_to_cleanup"] else "retained"
        self.save(directory, run)

    def measure_case(self, directory: Path, run: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
        preset, family = run["preset"]["id"], run["preset"]["family"]
        stage = directory / "cases" / case["id"]
        if stage.exists():
            # An attempt that never reached run.json, for example after a power
            # loss. Keep its files; the new attempt is recorded as a repeat.
            kept = stage.with_name(f"{case['id']}.unrecorded-{utc_now().replace(':', '')}")
            stage.rename(kept)
        stage.mkdir(parents=True)
        limits = self.protocol["limits"]
        probe = ManagedProbe(temper=self.temper.path, root=directory / "temper", installation=INSTALLATION,
                             execution_lock=directory / "execution.lock.json", generation=run["preparation"]["generation"],
                             listen=free_listen(), log_dir=stage / "process",
                             watch_spec=watch_spec(self.protocol, run["machine"], family),
                             router_ready_seconds=30, log_bytes_max=limits["process_log_bytes_max"])
        row: dict[str, Any] = {"id": case["id"], "status": "unmeasured", "failure": None, "attempted_at": utc_now(),
                               "unrecorded_attempts": sorted(path.name for path in stage.parent.glob(case["id"] + ".unrecorded-*"))}
        memory = loaded = None
        resources: dict[str, Any] = {}
        started = time.monotonic()
        try:
            probe.start()
            monitored_call(probe, f"/upstream/{preset}/health", {}, limits["startup_seconds"], method="GET")
            row["startup_seconds"] = time.monotonic() - started
            if not probe.observe_engine():
                raise ProbeError("the engine did not start for its health check")
            loaded = watcher.sample_macos(probe.full_binding, self.protocol["process_watch"]["command_timeout_seconds"])
            if family == "splash":
                source = f"/upstream/{preset}/status"
                native = self.protocol["native_memory"]
                memory = SplashMemory(lambda: monitored_call(probe, source, {}, native["timeout_seconds"], method="GET"),
                                      source, native["interval_seconds"])
                memory.capture("loaded")
            body = {**case["request"], "model": preset}
            atomic_write(stage / "request.json", canonical_json(body))
            stream = CodingStream(stage / "response.sse")
            try:
                response = monitored_call(probe, "/v1/chat/completions", body, limits["request_seconds"],
                                          streaming=True, stream_reader=stream,
                                          observer=memory.poll if memory else None)
            except (Exception, KeyboardInterrupt) as error:
                row["failure"] = {"kind": classify_failure(error), "message": str(error)[:2000]}
                response = stream.snapshot()
            atomic_write(stage / "response.json", canonical_json(response))
            row.update(rates(response), task_seconds=response.get("service_seconds"),
                       first_token_seconds=response.get("first_token_seconds"),
                       finish_reason=response.get("finish_reason"), stream_complete=response.get("stream_complete"),
                       reasoning_characters=len(response["message"]["reasoning"]))
            if not row["failure"] and response.get("stream_complete") and response.get("finish_reason") != "length":
                try:
                    checked = patch_check(response, directory / "fixture" / "base", case["edit_scope"])
                    atomic_write(stage / "patch.diff", checked["diff"].encode())
                    row["patch"] = {"changed_files": checked["changed_files"], "diff": checked["diff"],
                                    "diff_sha256": digest(checked["diff"].encode())}
                except InvalidPatch as error:
                    row["submission_error"] = str(error)
        except (Exception, KeyboardInterrupt) as error:
            row["failure"] = {"kind": classify_failure(error), "message": str(error)[:2000]}
        finally:
            try:
                if memory:
                    memory.capture("final")
            except (Exception, KeyboardInterrupt):
                pass
            finally:
                resources = probe.finish()
        row["safe_to_cleanup"] = bool(resources.get("safe_to_cleanup"))
        row["memory"] = memory_record(loaded, resources, memory.result() if memory else None)
        row["resources"] = resources
        if resources.get("stop_reasons") and not row["failure"]:
            row["failure"] = {"kind": "stopped-by-watch", "message": json.dumps(resources["stop_reasons"])[:2000]}
        row["status"] = case_status(row)
        row["measurement_valid"] = bool(row.get("stream_complete")) and not row["failure"] and resources_valid(resources)
        return row

    def cleanup(self, directory: Path, run: dict[str, Any]) -> None:
        self.say("Removing the engine and the run's Temper folder ...")
        self.temper.remove(directory / "execution.lock.json", directory / "temper", INSTALLATION)
        for name in ("temper", "hf-cache", "fixture"):
            path = directory / name
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
        run.update(state="complete", cleanup="complete")


def memory_record(loaded: dict[str, Any] | None, resources: dict[str, Any], native: dict[str, Any] | None) -> dict[str, Any]:
    engine_loaded = ((loaded or {}).get("roles") or {}).get("engine") or {}
    engine_peak = (resources.get("roles") or {}).get("engine") or {}
    baseline, loaded_wired = resources.get("wired_baseline_bytes"), (loaded or {}).get("wired_bytes")
    record = {
        "engine_loaded_wired_bytes": None if baseline is None or loaded_wired is None else max(0, loaded_wired - baseline),
        "engine_peak_wired_bytes": resources.get("wired_growth_bytes") or None,
        "engine_loaded_footprint_bytes": engine_loaded.get("current_footprint_bytes"),
        "engine_loaded_rss_bytes": engine_loaded.get("rss_bytes"),
        "engine_peak_footprint_bytes": engine_peak.get("peak_footprint_bytes_max") or None,
        "engine_rss_max_bytes": engine_peak.get("rss_bytes_max") or None,
        "swap_growth_bytes": resources.get("swap_growth_bytes"),
        "loaded_roles": (loaded or {}).get("roles"),
        "peak_roles": resources.get("roles"),
    }
    if native is not None:
        record["splash_native"] = native
    return record


def _interrupt(_signal, _frame):
    raise KeyboardInterrupt("stopped by SIGTERM")


def command(arguments, repository: Path) -> int:
    if arguments.background:
        if not arguments.yes:
            raise Refusal("--background starts without asking, so it needs --yes; read the plan from --plan first")
        from . import background
        forwarded = ["--yes"]
        if arguments.preset:
            forwarded += ["--preset", arguments.preset]
        if arguments.preset_file:
            forwarded += ["--preset-file", str(arguments.preset_file.resolve())]
        if arguments.temper:
            forwarded += ["--temper", str(arguments.temper.resolve())]
        return background.start(repository, forwarded)
    temper = Temper(locate(arguments.temper))
    check = BasicCheck(repository, temper)
    if arguments.preview:
        return check.preview(arguments.preset, arguments.preset_file)
    return check.start(arguments.preset, preset_path=arguments.preset_file, assume_yes=arguments.yes,
                       plan_only=arguments.plan)
