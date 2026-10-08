"""Select one frozen configuration; each task remains its own retained action."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

from ...catalog import Refusal, canonical_json, digest
from .splash_study import SplashStudy, bucket

SELECTOR = "qwen-machine-study@6"
SUPPORTED_SELECTORS = {"qwen-machine-study@5", SELECTOR}
SCHEMA = "field-kit-qwen-chunk-study/v2"


def configuration(protocol, identity):
    for item in protocol["configurations"]:
        if item["id"] == identity:
            return item
    raise Refusal(f"unknown configuration {identity!r}")


def selected_cells(protocol, identity):
    selected = configuration(protocol, identity)
    by_id = {cell["id"]: cell for cell in protocol["cells"]}
    return [by_id[name] for name in selected["cells"]]


def available_configurations(protocol, facts):
    selected = bucket(facts)
    return [item for item in protocol["configurations"] if selected in item["buckets"]]


def configuration_from_selector(selector):
    prefix = "qwen-chunk-"
    if not isinstance(selector, str) or not selector.startswith(prefix) or selector[-2:] not in ("@5", "@6"):
        raise Refusal("session is not a supported configuration run")
    suffix = selector[-2:]
    return selector[len(prefix):-len(suffix)]


def select_package(source, identity):
    """Derive an exact run package from a frozen bundle and an explicit choice.

    The derived identity names the configuration. Its origin retains the source
    bundle hash, and ordinary plans bind its complete bytes before any effects.
    Review uses this same pure selection against the supplied source bundle.
    """
    if source.selector not in SUPPORTED_SELECTORS:
        raise Refusal("configuration selection requires a frozen Qwen chunk bundle")
    protocol = json.loads(source.files["protocol.json"])
    selected = configuration(protocol, identity)
    cells = selected_cells(protocol, identity)
    package = copy.deepcopy(source.package)
    package["id"] = "qwen-chunk-" + identity
    package["origin"] = {"kind": "configuration-selection", "source": source.selector,
                         "source_sha256": source.package_sha256, "configuration": identity}
    package["summary"] = selected["summary"]
    package["profile"]["layout"] = cells[0]["preset"]
    package["mechanics"]["mode"] = cells[0]["preset"]
    lock_path = cells[0]["lock"]
    old_lock = package["execution_lock"]
    protocols = {item["path"]: item for item in package["mechanics"]["protocols"]}
    protocols[old_lock["path"]] = old_lock
    package["execution_lock"] = protocols.pop(lock_path)
    package["mechanics"]["protocols"] = [protocols[name] for name in sorted(protocols)]
    investigation = package["investigation"]
    actions = {item["id"]: item for item in investigation["actions"]}
    ids = [cell["id"] for cell in cells] + ["finish-study"]
    investigation["actions"] = [actions[name] for name in sorted(ids)]
    investigation["initial_action"] = {"id": ids[0], "parameters": {}}
    investigation["total_attempts_max"] = len(ids)
    investigation["total_runtime_minutes_max"] = sum(actions[name]["runtime_minutes_max"] for name in ids)
    package["cost"].update(selected["cost"])
    package["cost"]["live_runtime_minutes_max"] = investigation["total_runtime_minutes_max"]
    data = canonical_json(package)
    changes = {"package": package, "package_data": data}
    if hasattr(source, "reference"):
        changes["reference"] = {**source.reference, "id": package["id"], "package_sha256": digest(data)}
    return replace(source, **changes)


class ChunkStudy(SplashStudy):
    def __init__(self, arguments, package_root):
        super().__init__(arguments, package_root)
        package = json.loads((Path(package_root) / "package.json").read_bytes())
        identity = configuration_from_selector(self.session["package"]["selector"])
        if package["origin"]["configuration"] != identity:
            raise Refusal("configuration differs from the consented package")
        allowed = available_configurations(self.protocol, self.session["machine_facts"])
        if identity not in {item["id"] for item in allowed}:
            raise Refusal("configuration is not applicable to this machine")
        self.cells = selected_cells(self.protocol, identity)
