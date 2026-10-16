"""The Flask task refuses patches outside its edit scope."""
import json
from pathlib import Path
import tempfile
import unittest

from fieldkit_runtime.coding.evaluation import unpack
from fieldkit_runtime.coding.patches import InvalidPatch, staged_texts, submission

TASK = Path(__file__).resolve().parents[1] / "tasks" / "flask-coding"


class PatchScopeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_patch_scope_rejects_test_configuration_symlinks_and_ambiguous_json(self):
        base=unpack(TASK/"flask.tar.gz",self.root/"fixture")
        case=json.loads((TASK/"cases.json").read_bytes())["cases"][0]
        for path in ("../escape.py","tests/conftest.py","tests/.hidden.py","/tmp/x.py"):
            with self.assertRaises(InvalidPatch): staged_texts({"edits":[],"new_files":[{"path":path,"content":"x"}]},base,case["edit_scope"])
        (base/"tests/test_candidate_async_stream_link.py").symlink_to(self.root/"outside")
        with self.assertRaises(InvalidPatch): staged_texts({"edits":[],"new_files":[{"path":"tests/test_candidate_async_stream_link.py","content":"x"}]},base,case["edit_scope"])
        with self.assertRaises(InvalidPatch): submission({"message":{"tool_calls":[{"function":{"name":"submit_patch","arguments":'{"edits":[],"edits":[]}'}}]}})
