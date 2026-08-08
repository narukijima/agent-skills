import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "logic-pro" / "scripts" / "logic_guard.py"
SPEC = importlib.util.spec_from_file_location("logic_guard", SCRIPT)
logic_guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(logic_guard)


def base_request(operation="transport.play", arguments=None, project="/tmp/Song.logicx"):
    verifier = logic_guard.VERIFY_CAPABILITIES.get(operation)
    capabilities = [operation] if verifier is None else [operation, verifier]
    return {
        "operation": operation,
        "arguments": arguments or {},
        "authorization": {"write": True},
        "environment": {
            "logic_running": True,
            "screen_unlocked": True,
            "accessibility_authorized": True,
            "modal_dialog": False,
            "capabilities": capabilities,
            "expected_project": project,
            "current_project": project,
            "current_project_unavailable": False,
            "window_project_name": Path(project).name,
            "available_track_ids": ["track-1"],
            "available_track_indexes": [0],
            "available_instruments": ["com.apple.logic.es2"],
        },
        "policy": {"allowed_input_roots": ["/tmp"], "allowed_output_roots": ["/tmp"]},
    }


class LogicGuardTests(unittest.TestCase):
    def setUp(self):
        self.project_directory = tempfile.TemporaryDirectory(suffix=".logicx")
        self.project = self.project_directory.name

    def tearDown(self):
        self.project_directory.cleanup()

    def request(self, operation="transport.play", arguments=None):
        return base_request(operation, arguments, self.project)

    def test_authorizes_allowlisted_bound_single_operation(self):
        result = logic_guard.preflight(self.request())
        self.assertTrue(result["ok"])
        self.assertEqual(result["project_proof"], "project-url")
        self.assertEqual(len(result["request_sha256"]), 64)

    def test_rejects_unallowlisted_operation(self):
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(self.request("track.delete"))

    def test_rejects_wrong_project(self):
        request = self.request()
        request["environment"]["current_project"] = "/tmp/Other.logicx"
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(request)

    def test_allows_exact_window_name_only_when_project_url_unavailable(self):
        request = self.request()
        request["environment"]["current_project"] = None
        request["environment"]["current_project_unavailable"] = True
        result = logic_guard.preflight(request)
        self.assertEqual(result["project_proof"], "window-name-fallback")
        request["environment"]["window_project_name"] = "Wrong.logicx"
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(request)

    def test_rejects_locked_or_unknown_screen(self):
        for value in (False, None):
            request = self.request()
            request["environment"]["screen_unlocked"] = value
            with self.assertRaises(logic_guard.GuardFailure):
                logic_guard.preflight(request)

    def test_midi_import_requires_existing_allowed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            midi = root / "phrase.mid"
            midi.write_bytes(b"MThd")
            request = self.request("midi.import_file", {"path": str(midi)})
            request["policy"]["allowed_input_roots"] = [str(root)]
            self.assertTrue(logic_guard.preflight(request)["ok"])
            request["arguments"]["path"] = str(root / "missing.mid")
            with self.assertRaises(logic_guard.GuardFailure):
                logic_guard.preflight(request)

    def test_save_as_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Copy.logicx"
            target.mkdir()
            request = self.request("project.save_as", {"path": str(target)})
            request["policy"]["allowed_output_roots"] = [directory]
            with self.assertRaises(logic_guard.GuardFailure):
                logic_guard.preflight(request)

    def test_instrument_must_come_from_observed_list(self):
        request = self.request("track.set_instrument", {"track_id": "track-1", "instrument_id": "third.party.synth"})
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(request)

    def test_track_selection_must_use_an_observed_track(self):
        request = self.request("track.select", {"track_id": "missing-track"})
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(request)
        request["arguments"] = {"track_index": 0}
        self.assertTrue(logic_guard.preflight(request)["ok"])

    def test_success_without_independent_readback_is_unknown(self):
        result = logic_guard.classify({"dispatch": {"status": "success", "definitive": True}})
        self.assertEqual(result["class"], "B")
        self.assertFalse(result["gui_fallback_eligible"])

    def test_fresh_logic_readback_is_verified_success(self):
        result = logic_guard.classify(
            {
                "dispatch": {"status": "success", "definitive": True},
                "readback": {"fresh": True, "source": "logic-mcp-state", "matches_expected": True},
            }
        )
        self.assertEqual(result["class"], "A")

    def test_only_definitive_failure_is_gui_fallback_eligible(self):
        confirmed = logic_guard.classify({"dispatch": {"status": "failed", "definitive": True}})
        ambiguous = logic_guard.classify({"dispatch": {"status": "failed", "definitive": False}})
        self.assertEqual(confirmed["class"], "C")
        self.assertTrue(confirmed["gui_fallback_eligible"])
        self.assertEqual(ambiguous["class"], "B")

    def test_cli_rejection_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(json.dumps(self.request("track.delete")), encoding="utf-8")
            self.assertEqual(logic_guard.main(["preflight", "--request", str(request_path)]), 2)

    def test_write_requires_a_readback_capability(self):
        request = self.request()
        request["environment"]["capabilities"] = ["transport.play"]
        with self.assertRaises(logic_guard.GuardFailure):
            logic_guard.preflight(request)


if __name__ == "__main__":
    unittest.main()
