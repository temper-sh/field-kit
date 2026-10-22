"""The approval document: exact candidates, limits, storage and the machine effect.

Building it is read-only. Temper's own check and plan are used where available;
the owner approves exactly what this document shows.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from . import lockview, machine, records, storage, subjects
from .adapters import engines, interfaces
from .common import IDENTITY, Refusal, digest, read_regular
from .context import DEFAULT
from .material import validate_variant
from .measurements import policy_for
from .process import CommandFailure

GIB = 1024**3

def identity(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Refusal("use a plain lowercase study/candidate ID with dots or dashes")
    return value

def candidate(temper, path):
    raw = read_regular(path, 16 * 1024**2)
    lock = json.loads(raw)
    execution = temper.inspect(path)
    name = identity(execution["preset"])
    interface = interfaces.resolve(lockview.interface(lock))
    adapter = engines.from_lock(lock)
    return {"id": name, "subject": "temper", "lock": raw.decode(), "lock_sha256": digest(raw), "execution": execution,
            "interface": interface.IDENTIFIER, "family": adapter.family, "engine_adapter": adapter.identifier}

def hf_environment(cache):
    # Only the hub cache moves; HF_HOME, and with it the user's token, stays.
    return {**os.environ, "HF_HUB_CACHE": str(cache), "HF_HUB_DISABLE_TELEMETRY": "1"}

def assess(temper, authoring_root, preset, environment, can_plan):
    """Temper's own check and install plan for a user preset; Temper refusals stop the plan."""
    if not (authoring_root / "presets.d" / f"{preset}.json").is_file():
        return {"check": None, "plan": None, "note": "not a user preset in the authoring root"}
    try:
        checked = temper.check_preset(preset, authoring_root, environment)["presets"][0]
    except (CommandFailure, KeyError, IndexError) as error:
        raise Refusal(f"Temper preset check failed for {preset}: {error}") from error
    result = {"check": {key: checked.get(key) for key in
              ("valid", "error", "fits", "refusals", "memory_bytes", "model_bytes", "memory_estimate")},
              "plan": "unavailable in this Temper; Field Kit shows known file sizes"}
    if not checked.get("valid") or checked.get("refusals") or checked.get("fits") is False:
        raise Refusal(f"Temper refuses {preset} on this Mac: " +
                      "; ".join(checked.get("refusals") or [checked.get("error") or "estimate exceeds the budget"]))
    if can_plan:
        try:
            plan = temper.plan_preset(preset, authoring_root, environment)["plan"]
        except (CommandFailure, KeyError) as error:
            raise Refusal(f"Temper preset plan failed for {preset}: {error}") from error
        result["plan"] = {key: plan.get(key) for key in ("remaining_download_bytes", "remaining_disk_bytes",
                          "free_disk_bytes", "can_prepare", "refusals")}
        if plan.get("can_prepare") is False:
            raise Refusal(f"Temper cannot prepare {preset} here: " + "; ".join(plan.get("refusals") or ["see preset plan"]))
    return result

def preview(temper, locks, brief, *, hours=8, download_gib=100, max_context=262144,
            request_seconds=1800, trial_seconds=14400, authoring_root=None, user_root=None,
            weights_cache=None, weights_cleanup="added", root=None, context=DEFAULT, engines_local=()):
    if not (locks or engines_local) or not 0 < hours <= 168 or download_gib < 0 or max_context < 512 \
            or request_seconds < 1 or trial_seconds < 1:
        raise Refusal("name candidates and positive time/context limits (download allowance may be zero)")
    if weights_cleanup not in ("added", "keep"):
        raise Refusal("weights cleanup is added or keep")
    text = read_regular(brief).decode()
    if not text.strip():
        raise Refusal("write the study question and scope in the brief")
    candidates = [candidate(temper, path) for path in locks]
    local = [subjects.local_candidate(path) for path in engines_local]
    if len({row["id"] for row in candidates + local}) != len(candidates + local):
        raise Refusal("candidate IDs must be distinct")
    documents = [json.loads(row["lock"]) for row in candidates]
    for document in documents:
        validate_variant(document, document, max_context)
    for row in local:
        subjects.validate_local_variant(row, row, max_context)
    cache = Path(weights_cache).absolute() if weights_cache else storage.default_cache()
    if candidates:  # local engines download nothing
        storage.require_cache(cache, weights_cache is not None)
    weights = storage.weights_plan(cache, documents, weights_cleanup)
    software = lockview.software_bytes(documents)
    planned = weights["absent_bytes"] + software
    if planned > download_gib * GIB:
        raise Refusal("planned downloads exceed the proposed allowance; reuse/narrow candidates or review a larger scope")
    # Only Temper candidates use the study root and the weights cache; local files stay where they are.
    locations = {"root": context.volume(root) if root and candidates else None, "weights_cache": context.volume(cache),
                 "local_files": {row["id"]: {name: context.volume(record["path"]) for name, record in row["files"].items()}
                                 for row in local}}
    if locations["root"]:
        storage.require_apfs(locations["root"], "the study root")
    needs = storage.require_space(locations["root"], locations["weights_cache"], weights["absent_bytes"], software)
    assessments = {}
    if authoring_root:
        authoring_root = Path(authoring_root).absolute()
        can_plan = temper.supports(["preset", "plan"])
        assessments = {row["id"]: assess(temper, authoring_root, row["id"], hf_environment(cache), can_plan)
                       for row in candidates}
    layout = machine.observe(temper, str(Path(user_root).absolute()) if user_root else None)
    if layout["running"] and not layout["layout"]:
        raise Refusal("a Temper layout runs but reports no ID; stop it with temper layout stop before the study")
    layout["effect"] = ("stop before measuring; restore at close" if layout["running"] else
                        "none" if layout["running"] is False else "unknown: " + layout["error"])
    return {"schema": records.STUDY_SCHEMA, "brief": text, "temper": temper.identity(), "machine": temper.machine_facts(),
            "resource_policy": policy_for(engines.resolve(row["engine_adapter"]) for row in candidates + local),
            "budgets": {"seconds": int(hours * 3600), "download_bytes": int(download_gib * GIB),
                        "context_tokens": max_context, "request_seconds": request_seconds, "trial_seconds": trial_seconds},
            "known_material_bytes": lockview.declared_material_bytes(documents),
            "planned_download_bytes": planned,
            "download_limit_scope": "Approved material allowance, not a network byte meter. Known file sizes, less weights already in the cache; unlisted dependencies/retries excluded. Review Temper's plan and unknowns before approval.",
            "disk_needs_bytes": needs,
            "cache": f"{cache}: " + ("exact files this study adds are removed at close" if weights_cleanup == "added"
                                     else "kept at close"),
            "weights": weights, "weights_cache_explicit": weights_cache is not None, "storage": locations,
            "temper_assessment": assessments, "user_layout": layout,
            "restore_command": machine.restore_command(layout), "instruction": machine.INSTRUCTION,
            "other_processes": machine.large_processes(),
            "candidates": candidates + local}

def summary(document):
    candidates = []
    for row in document["candidates"]:
        common = {"id": row["id"], "subject": row["subject"], "interface": row["interface"], "family": row["family"],
                  "engine_adapter": row["engine_adapter"]}
        if row["subject"] == "local":
            candidates.append({**common, "spec_sha256": row["spec_sha256"], **subjects.describe_local(row)})
            continue
        lock = json.loads(row["lock"])
        candidates.append({**common, "lock_sha256": row["lock_sha256"], "installation": lockview.installation(lock),
            **lockview.describe(lock), "temper_assessment": document["temper_assessment"].get(row["id"])})
    weights = document["weights"]
    return {"temper": document["temper"], "machine": document["machine"], "budgets": document["budgets"],
            "known_material_bytes": document["known_material_bytes"],
            "planned_download_bytes": document["planned_download_bytes"],
            "download_limit_scope": document["download_limit_scope"], "disk_needs_bytes": document["disk_needs_bytes"],
            "cache": document["cache"], "storage": document["storage"],
            "weights_to_download": [{"repo": row["repo"], "path": row["path"], "bytes": row["bytes"]}
                                    for row in weights["files"] if not row["present_before"]],
            "user_layout": document["user_layout"], "restore_command": document["restore_command"],
            "instruction": document["instruction"],
            "other_processes": {"rows": document["other_processes"],
                                "note": "Read-only; RSS can omit GPU memory. Close other AI apps before measuring."},
            "resource_policy": document["resource_policy"],
            "candidates": candidates, "brief": document["brief"]}
