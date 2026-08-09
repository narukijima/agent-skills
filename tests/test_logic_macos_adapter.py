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


def control(title: str, *, role="AXButton", value=None, press=True) -> dict:
    return {
        "role": role,
        "title": title,
        "description": None,
        "help": None,
        "identifier": None,
        "value": value,
        "enabled": True,
        "actions": ["AXPress"] if press else [],
    }


def snapshot(
    project: str,
    *,
    playing=False,
    bundle_identifier="com.apple.mobilelogic",
    window_discovery_source="process.windows",
    window_set_complete=True,
) -> dict:
    controls = [control("Play", value=playing)]
    controls.append(control("Stop") if playing else control("Go to Beginning"))
    return {
        "ok": True,
        "logic_running": True,
        "bundle_identifier": bundle_identifier,
        "screen_unlocked": True,
        "accessibility_authorized": True,
        "modal_dialog": False,
        "frontmost": True,
        "window_discovery_source": window_discovery_source,
        "window_set_complete": window_set_complete,
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

    def dispatch_transport(self, operation, expected_project, bundle_identifier):
        self.dispatch_calls.append((operation, expected_project, bundle_identifier))
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
        self.assertEqual(
            document["supported_bundle_identifiers"],
            ["com.apple.mobilelogic", "com.apple.logic10"],
        )
        self.assertEqual(document["supported_transport_control_roles"], ["AXButton", "AXCheckBox"])
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
        self.assertEqual(result["data"]["bundle_identifier"], "com.apple.mobilelogic")
        self.assertEqual(result["data"]["window_discovery_source"], "process.windows")
        self.assertTrue(result["data"]["window_set_complete"])
        self.assertIn("transport.play", result["data"]["capabilities"])
        self.assertIn("transport.stop", result["data"]["capabilities"])

    def test_foreground_empty_window_snapshot_fails_closed(self):
        state = snapshot(self.project)
        state["windows"] = []
        state["controls"] = []
        adapter = adapter_module.ReferenceAdapter(FakeBackend(state))
        observed = adapter.observe("project.current")
        self.assertTrue(observed["data"]["current_project_unavailable"])
        self.assertIsNone(observed["data"]["current_project"])
        self.assertEqual(observed["data"]["capabilities"], ["app.status"])
        dispatched = adapter.dispatch(preflight("transport.play", self.project))
        self.assertEqual(dispatched["error"]["kind"], "project_mismatch")

    def test_complete_axwindows_fallback_restores_project_transport_and_dispatch(self):
        state = snapshot(self.project, window_discovery_source="AXWindows")
        backend = FakeBackend(state, {"ok": True, "performed": True, "window_discovery_source": "AXWindows"})
        adapter = adapter_module.ReferenceAdapter(backend)
        observed = adapter.observe("transport.state")
        self.assertEqual(observed["data"]["current_project"], str(Path(self.project).resolve()))
        self.assertEqual(observed["data"]["window_discovery_source"], "AXWindows")
        self.assertTrue(observed["data"]["window_set_complete"])
        self.assertIn("transport.play", observed["data"]["capabilities"])
        dispatched = adapter.dispatch(preflight("transport.play", self.project))
        self.assertTrue(dispatched["ok"])
        self.assertEqual(dispatched["dispatch"]["window_discovery_source"], "AXWindows")

    def test_incomplete_main_window_fallback_is_read_only(self):
        state = snapshot(
            self.project,
            window_discovery_source="AXMainWindow",
            window_set_complete=False,
        )
        state["modal_dialog"] = None
        backend = FakeBackend(state)
        adapter = adapter_module.ReferenceAdapter(backend)
        observed = adapter.observe("transport.state")
        self.assertEqual(observed["data"]["current_project"], str(Path(self.project).resolve()))
        self.assertIn("transport.state", observed["data"]["capabilities"])
        self.assertNotIn("transport.play", observed["data"]["capabilities"])
        dispatched = adapter.dispatch(preflight("transport.play", self.project))
        self.assertEqual(dispatched["error"]["kind"], "window_set_incomplete")
        self.assertEqual(backend.dispatch_calls, [])

    def test_japanese_control_labels_are_mapped_without_coordinates(self):
        state = snapshot(self.project)
        state["controls"] = [control("再生", value=False), control("先頭へ移動")]
        transport = adapter_module.transport_from_controls(state["controls"])
        self.assertFalse(transport["is_playing"])
        self.assertTrue(transport["play_control_available"])

    def test_japanese_checkbox_play_control_is_observable_and_dispatchable(self):
        state = snapshot(self.project)
        state["controls"] = [
            control("再生", role="AXCheckBox", value=False),
            control("先頭へ移動"),
        ]
        transport = adapter_module.transport_from_controls(state["controls"])
        self.assertFalse(transport["is_playing"])
        self.assertEqual(transport["state_basis"], "play-control-value")
        self.assertTrue(transport["play_control_available"])
        self.assertIn("transport.play", adapter_module.runtime_capabilities(state))
        backend = FakeBackend(state)
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, "com.apple.mobilelogic")])

    def test_checkbox_without_axpress_does_not_expose_play_dispatch(self):
        state = snapshot(self.project)
        state["controls"] = [
            control("再生", role="AXCheckBox", value=False, press=False),
            control("先頭へ移動"),
        ]
        transport = adapter_module.transport_from_controls(state["controls"])
        self.assertFalse(transport["is_playing"])
        self.assertFalse(transport["play_control_available"])
        self.assertNotIn("transport.play", adapter_module.runtime_capabilities(state))

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
        self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, "com.apple.mobilelogic")])

    def test_current_and_legacy_bundle_identifiers_use_the_same_contract(self):
        for bundle_identifier in adapter_module.SUPPORTED_LOGIC_BUNDLE_IDS:
            with self.subTest(bundle_identifier=bundle_identifier):
                backend = FakeBackend(snapshot(self.project, bundle_identifier=bundle_identifier))
                adapter = adapter_module.ReferenceAdapter(backend)
                observed = adapter.observe("app.status")
                self.assertTrue(observed["data"]["logic_running"])
                self.assertEqual(observed["data"]["bundle_identifier"], bundle_identifier)
                dispatched = adapter.dispatch(preflight("transport.play", self.project))
                self.assertTrue(dispatched["ok"])
                self.assertEqual(dispatched["dispatch"]["bundle_identifier"], bundle_identifier)
                self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, bundle_identifier)])

    def test_snapshot_and_action_share_central_bundle_discovery(self):
        snapshot_source = adapter_module.render_jxa(adapter_module.SNAPSHOT_JXA)
        action_source = adapter_module.render_jxa(
            adapter_module.ACTION_JXA,
            {
                "REQUESTED_OPERATION": "transport.play",
                "EXPECTED_PROJECT": self.project,
                "EXPECTED_BUNDLE_ID": "com.apple.mobilelogic",
            },
        )
        for bundle_identifier in adapter_module.SUPPORTED_LOGIC_BUNDLE_IDS:
            self.assertIn(bundle_identifier, snapshot_source)
            self.assertIn(bundle_identifier, action_source)
        self.assertIn("findLogicProcess(systemEvents, null)", snapshot_source)
        self.assertIn("findLogicProcess(systemEvents, EXPECTED_BUNDLE_ID)", action_source)

    def test_snapshot_and_action_share_window_discovery_fallbacks(self):
        snapshot_source = adapter_module.render_jxa(adapter_module.SNAPSHOT_JXA)
        action_source = adapter_module.render_jxa(
            adapter_module.ACTION_JXA,
            {
                "REQUESTED_OPERATION": "transport.play",
                "EXPECTED_PROJECT": self.project,
                "EXPECTED_BUNDLE_ID": "com.apple.mobilelogic",
            },
        )
        for source in (
            "process.windows",
            "AXWindows",
            "process.uiElements.AXWindow",
            "process.entireContents.AXWindow",
            "AXMainWindow",
            "AXFocusedWindow",
        ):
            self.assertIn(source, snapshot_source)
            self.assertIn(source, action_source)
        self.assertIn("discoverProcessWindows(process)", snapshot_source)
        self.assertIn("discoverProcessWindows(process)", action_source)
        self.assertIn('reason: "window_set_incomplete"', action_source)

    def test_snapshot_and_action_jxa_share_supported_transport_control_roles(self):
        snapshot_source = adapter_module.render_jxa(adapter_module.SNAPSHOT_JXA)
        action_source = adapter_module.render_jxa(
            adapter_module.ACTION_JXA,
            {
                "REQUESTED_OPERATION": "transport.play",
                "EXPECTED_PROJECT": self.project,
                "EXPECTED_BUNDLE_ID": "com.apple.mobilelogic",
            },
        )
        for role in adapter_module.SUPPORTED_TRANSPORT_CONTROL_ROLES:
            self.assertIn(role, snapshot_source)
            self.assertIn(role, action_source)
        self.assertIn("SUPPORTED_TRANSPORT_CONTROL_ROLES.includes", snapshot_source)
        self.assertIn("SUPPORTED_TRANSPORT_CONTROL_ROLES.includes", action_source)
        self.assertNotIn('role(), null)) === "AXButton"', action_source)
        self.assertIn('reason: "ax_press_not_supported"', action_source)

    def test_unrecognized_observed_bundle_is_rejected_before_action(self):
        backend = FakeBackend(snapshot(self.project, bundle_identifier="com.example.logic"))
        result = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertEqual(result["error"]["kind"], "unsupported_logic_bundle")
        self.assertEqual(backend.dispatch_calls, [])

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
