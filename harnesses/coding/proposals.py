"""Identity and retention of tested proposals within one coding attempt."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import patches
from fieldkit_runtime.common import canonical_json, digest

LEGACY = "complete-v1"
RETAINED = "retained-v2"
PROTOCOLS = (LEGACY, RETAINED)
REFERENCE_TOOL = {"type": "function", "function": {
    "name": "submit_tested_patch",
    "description": "Submit an exact retained test_patch proposal by ID, without repeating its edits or files. Independent grading and source review still apply.",
    "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": {"proposal_id": {"type": "string"}, "explanation": {"type": "string"}},
        "required": ["proposal_id", "explanation"]}}}


def tree_identity(root):
    """Hash relative names and contents, never machine paths or timestamps."""
    entries = {}
    for path in sorted(Path(root).rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[name] = {"symlink": path.readlink().as_posix()}
        elif path.is_file():
            entries[name] = {"sha256": digest(path.read_bytes())}
    return digest(canonical_json(entries))


def reference(response):
    calls = response.get("message", {}).get("tool_calls") or []
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "submit_tested_patch":
        raise patches.InvalidPatch("Use exactly one submit_tested_patch call.")
    arguments = calls[0]["function"].get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments, object_pairs_hook=patches.unique_object)
        except ValueError as error:
            raise patches.InvalidPatch("Invalid proposal reference JSON: " + str(error)) from error
    if (not isinstance(arguments, dict) or set(arguments) != {"proposal_id", "explanation"}
            or not isinstance(arguments["proposal_id"], str) or not arguments["proposal_id"]
            or not isinstance(arguments["explanation"], str)):
        raise patches.InvalidPatch("Expected a nonempty proposal_id and a text explanation only.")
    return arguments


class RetainedProposals:
    """Only successfully executed test calls in this attempt may be referenced."""

    def __init__(self, base, case):
        self.base = Path(base)
        self.baseline = tree_identity(self.base)
        self.case = digest(canonical_json(case))
        self.items = {}

    def identify(self, arguments):
        if tree_identity(self.base) != self.baseline:
            raise patches.InvalidPatch("The original source changed after this attempt began.")
        content = {key: value for key, value in arguments.items() if key != "explanation"}
        return digest(canonical_json({"protocol": RETAINED, "baseline": self.baseline,
                                      "case": self.case, "proposal": content}))

    def remember(self, arguments, folder, metadata=None):
        """Retain before testing; replay uses the recorded pre-test identity."""
        folder = Path(folder)
        proposal_id = self.identify(arguments)
        candidate_sha256 = tree_identity(folder / "candidate")
        if metadata is not None and (metadata.get("status") != "tested"
                or metadata.get("proposal_id") != proposal_id
                or metadata.get("candidate_sha256") != candidate_sha256):
            raise patches.InvalidPatch("Retained proposal identity or tested source changed.")
        self.items[proposal_id] = {"arguments": copy.deepcopy(arguments), "folder": folder,
                                   "candidate_sha256": candidate_sha256}
        return {"proposal_id": proposal_id, "candidate_sha256": candidate_sha256}

    def resolve(self, proposal_id):
        item = self.items.get(proposal_id)
        if item is None:
            raise patches.InvalidPatch("Unknown proposal_id in this attempt; submit a complete proposal or use an ID returned by test_patch.")
        if (self.identify(item["arguments"]) != proposal_id
                or tree_identity(item["folder"] / "candidate") != item["candidate_sha256"]):
            raise patches.InvalidPatch("Retained tested source changed; test a complete proposal again.")
        return copy.deepcopy(item["arguments"]), item["folder"].name
