"""Offline protocol identity, artifact integrity and exact legacy-request controls."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1] / "harnesses/coding"
sys.path.insert(0, str(HARNESS))
import harness
import patches
import proposals
import quality


class RetainedProposalTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "base"
        self.base.mkdir()
        (self.base / "work.py").write_text("original\n")
        self.case = {"id": "phase-a", "edit_scope": {"existing_files": ["work.py"],
                                                    "new_file_prefixes": ["tests/"]}}
        self.arguments = {"explanation": "repair", "edits": [
            {"path": "work.py", "old_text": "original", "new_text": "corrected"}], "new_files": []}
        self.folder = self.root / "turn-001"
        patches.apply(self.arguments, self.base, self.folder / "candidate", self.case["edit_scope"])
        self.store = proposals.RetainedProposals(self.base, self.case)
        self.metadata = {"status": "tested", **self.store.remember(self.arguments, self.folder)}
        self.proposal_id = self.metadata["proposal_id"]

    def test_identity_is_reproducible_across_roots_and_explanations_and_binds_the_phase(self):
        other = self.root / "other-base"
        shutil.copytree(self.base, other)
        arguments = copy.deepcopy(self.arguments)
        arguments["explanation"] = "different prose"
        replay = proposals.RetainedProposals(other, self.case)
        self.assertEqual(replay.identify(arguments), self.proposal_id)
        phase = proposals.RetainedProposals(other, {**self.case, "id": "phase-b"})
        self.assertNotEqual(phase.identify(arguments), self.proposal_id)
        with self.assertRaises(patches.InvalidPatch):
            phase.resolve(self.proposal_id)

    def test_retention_and_resolution_do_not_allow_mutable_arguments_to_replace_the_artifact(self):
        self.arguments["edits"][0]["new_text"] = "unsubmitted change"
        selected, origin = self.store.resolve(self.proposal_id)
        self.assertEqual(origin, "turn-001")
        self.assertEqual(selected["edits"][0]["new_text"], "corrected")
        selected["edits"].clear()
        self.assertEqual(len(self.store.resolve(self.proposal_id)[0]["edits"]), 1)

    def test_changed_original_or_tested_artifact_cannot_be_submitted_by_reference(self):
        for target in (self.base / "work.py", self.folder / "candidate/work.py"):
            with self.subTest(target=target):
                original = target.read_bytes()
                target.write_text("changed outside the proposal\n")
                with self.assertRaises(patches.InvalidPatch):
                    self.store.resolve(self.proposal_id)
                target.write_bytes(original)

    def test_replay_checks_the_recorded_identity_and_pre_test_source(self):
        replay = proposals.RetainedProposals(self.base, self.case)
        replay.remember(self.arguments, self.folder, json.loads(json.dumps(self.metadata)))
        self.assertEqual(replay.resolve(self.proposal_id)[0], self.arguments)
        for field, value in (("status", "testing"), ("proposal_id", "wrong"), ("candidate_sha256", "wrong")):
            with self.subTest(field=field), self.assertRaises(patches.InvalidPatch):
                replay.remember(self.arguments, self.folder, {**self.metadata, field: value})

    def test_references_cannot_smuggle_replacement_edits_or_ambiguous_json(self):
        for arguments in ('{"proposal_id":"a","proposal_id":"b","explanation":"x"}',
                          {"proposal_id": self.proposal_id, "explanation": "x", "edits": []},
                          {"proposal_id": "", "explanation": "x"}):
            with self.subTest(arguments=arguments), self.assertRaises(patches.InvalidPatch):
                proposals.reference({"message": {"tool_calls": [{"function": {
                    "name": "submit_tested_patch", "arguments": arguments}}]}})


class LegacyRequestTest(unittest.TestCase):
    def test_complete_v1_requests_match_the_pre_fix_protocol_bytes(self):
        # Digests captured from the original implementation before this change.
        expected = {"original": "1e36d3fca97ec757549e42777fcddf2f6a76adc84db022fa7a9bb2d0c73343fb",
                    "feedback": "e775a0f68f4746b5ea5c49a116497dfa959cde63631c2258e2286f1415b68793",
                    "feedback-state": "5845361f2a49d6622807fd45596487be3856244dbae4232479980ea0f9441dd2"}
        for contract, sha256 in expected.items():
            with self.subTest(contract=contract):
                body = quality.request_for(harness.load_case("pagination-repair"), 6, contract,
                                           proposals.LEGACY)
                self.assertEqual(hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), sha256)


if __name__ == "__main__":
    unittest.main()
