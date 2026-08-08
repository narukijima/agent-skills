import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills" / "logic-pro" / "scripts" / "logic_macos_adapter.py"
SPEC = importlib.util.spec_from_file_location("logic_macos_adapter", SCRIPT)
adapter_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter_module)


def preflight(operation: str, project: str) -> dict:
    request = {"operation": operation, "arguments": {}, "expected_project": project}
    fingerprint = hashlib.sha256(adapter_module.canonical_json(request)).hexdigest()
    return {
        "ok": True,
        "classification": "authorized",
        "operation": operation,
        "impact": "write",
        "project_proof": "project-url",
        "request": request,
        "request_sha256": fingerprint,
    }


def control(title: str, *, value=None, press=True) -> dict:
    return {
        "role": "AXButton",
        "title": title,
        "description": None,
        "help": None,
        "identifier": None,
        "value": value,
        "enabled": True,
        "actions": ["AXPress"] if press else [],
    }


def snapshot(project: str, *, playing=False) -> dict:
    controls = [control("Play", value=playing)]
    controls.append(control("Stop") if playing else control("Go to Beginning"))
    return {
        "ok": True,
        "logic_running": True,
        "screen_unlocked": True,
        "accessibility_authorized": True,
        "modal_dialog": False,
        "frontmost": True,
        "windows": [{"title": Path(project).name, "document": project, "main": True}],
        "controls": controls,
    }


class FakeBackend:
    def __init__(self, state: dict, action=None):
        self.state = state
        self.action = action or {"ok": True, "performed": True, "already_satisfied": False}
        self.dispatch_calls = []

    def snapshot(self):
        return json.loads(json.dumps(self.state))

    def dispatch_transport(self, operation, expected_project):
        self.dispatch_calls.append((operation, expected_project))
        if isinstance(self.action, Exception):
            raise self.action
        return dict(self.action)


class LogicMacOSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.project_directory = tempfile.TemporaryDirectory(suffix=".logicx")
        self.project = self.project_directory.name

    def tearDown(self):
        self.project_directory.cleanup()

    def test_capability_matrix_names_every_allowlisted_operation(self):
        document = adapter_module.ReferenceAdapter(FakeBackend(snapshot(self.project))).capability_document()
        named = [row["operation"] for row in document["operations"]]
        self.assertEqual(named, list(adapter_module.ALL_OPERATIONS))
        self.assertEqual(
            document["capabilities"],
            ["app.status", "project.current", "transport.state", "transport.play", "transport.stop"],
        )
        unsupported = [row for row in document["operations"] if row["support"] == "not-implemented"]
        self.assertTrue(unsupported)
        self.assertTrue(all(row["reason"] for row in unsupported))

    def test_fresh_observation_has_source_timestamp_project_and_runtime_capabilities(self):
        adapter = adapter_module.ReferenceAdapter(FakeBackend(snapshot(self.project)))
        result = adapter.observe("transport.state")
        self.assertTrue(result["fresh"])
        self.assertEqual(result["source"], "logic-accessibility")
        self.assertTrue(result["observed_at"].endswith("Z"))
        self.assertEqual(result["data"]["current_project"], str(Path(self.project).resolve()))
        self.assertFalse(result["data"]["is_playing"])
        self.assertIn("transport.play", result["data"]["capabilities"])
        self.assertIn("transport.stop", result["data"]["capabilities"])

    def test_japanese_control_labels_are_mapped_without_coordinates(self):
        state = snapshot(self.project)
        state["controls"] = [control("再生", value=False), control("先頭へ移動")]
        transport = adapter_module.transport_from_controls(state["controls"])
        self.assertFalse(transport["is_playing"])
        self.assertTrue(transport["play_control_available"])

    def test_dispatch_rechecks_safety_and_project_before_action(self):
        state = snapshot(self.project)
        state["modal_dialog"] = True
        backend = FakeBackend(state)
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertEqual(result["dispatch"], {"status": "failed", "definitive": True})
        self.assertEqual(result["error"]["kind"], "modal_dialog_present")
        self.assertEqual(backend.dispatch_calls, [])

        other = tempfile.TemporaryDirectory(suffix=".logicx")
        self.addCleanup(other.cleanup)
        backend = FakeBackend(snapshot(other.name))
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertEqual(result["error"]["kind"], "project_mismatch")
        self.assertEqual(backend.dispatch_calls, [])

    def test_successful_dispatch_never_reuses_action_result_as_readback(self):
        backend = FakeBackend(snapshot(self.project))
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(result["dispatch"]["status"], "success")
        self.assertTrue(result["readback_required"])
        self.assertNotIn("readback", result)
        self.assertEqual(backend.dispatch_calls, [("transport.play", self.project)])

    def test_timeout_or_ambiguous_bridge_error_is_unknown(self):
        timeout = adapter_module.AdapterFailure(
            "timeout",
            "timed out",
            definitive=False,
            may_have_dispatched=True,
        )
        backend = FakeBackend(snapshot(self.project), timeout)
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertEqual(result["dispatch"], {"status": "unknown", "definitive": False})
        self.assertTrue(result["readback_required"])

    def test_unsupported_write_is_a_definitive_failure(self):
        backend = FakeBackend(snapshot(self.project))
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("project.save", self.project))
        self.assertEqual(result["dispatch"], {"status": "failed", "definitive": True})
        self.assertEqual(result["error"]["kind"], "unsupported_operation")
        self.assertEqual(backend.dispatch_calls, [])

    def test_modified_guard_fingerprint_is_rejected_before_action(self):
        backend = FakeBackend(snapshot(self.project))
        request = preflight("transport.play", self.project)
        request["request"]["operation"] = "transport.stop"
        result = adapter_module.ReferenceAdapter(backend).dispatch(request)
        self.assertEqual(result["error"]["kind"], "invalid_preflight")
        self.assertEqual(backend.dispatch_calls, [])

    def test_file_url_project_document_is_normalized(self):
        project = str(Path("/tmp/My Song.logicx").resolve())
        self.assertEqual(
            adapter_module.normalize_document_path("file:///tmp/My%20Song.logicx"),
            project,
        )


if __name__ == "__main__":
    unittest.main()
