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
        "process_identifier": 4242,
        "accessibility_backend": "SystemEvents",
        "screen_unlocked": True,
        "accessibility_authorized": True,
        "modal_dialog": False,
        "frontmost": True,
        "window_discovery_source": window_discovery_source,
        "window_discovery_diagnostic": None,
        "window_set_complete": window_set_complete,
        "focus_temporarily_changed": False,
        "transport_controls_observed": True,
        "transport_controls_complete": True,
        "windows": [{"title": Path(project).name, "document": project, "main": True}],
        "controls": controls,
    }


class FakeBackend:
    def __init__(self, state: dict, action=None):
        self.state = state
        self.action = action or {"ok": True, "performed": True, "already_satisfied": False}
        self.dispatch_calls = []
        self.snapshot_calls = []

    def snapshot(self, *, include_controls=True):
        self.snapshot_calls.append(include_controls)
        state = json.loads(json.dumps(self.state))
        if not include_controls:
            state["controls"] = []
            state["transport_controls_observed"] = False
            state["transport_controls_complete"] = False
        return state

    def dispatch_transport(self, operation, expected_project, bundle_identifier, process_identifier=None):
        self.dispatch_calls.append((operation, expected_project, bundle_identifier, process_identifier))
        if isinstance(self.action, Exception):
            raise self.action
        return dict(self.action)


class FakeNativeBridge:
    def __init__(self, state: dict, *, trusted=True, action=None, exit_error=None):
        self.state = state
        self.is_trusted = trusted
        self.action = action or {"ok": True, "performed": True, "already_satisfied": False}
        self.exit_error = exit_error
        self.snapshot_calls = []
        self.dispatch_calls = []
        self.factory_calls = []

    def __call__(self, process_identifier, timeout_seconds):
        self.factory_calls.append((process_identifier, timeout_seconds))
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.exit_error is not None:
            raise self.exit_error
        return None

    def trusted(self):
        return self.is_trusted

    def snapshot(self, *, include_controls, include_elements=False):
        self.snapshot_calls.append((include_controls, include_elements))
        return json.loads(json.dumps(self.state))

    def dispatch_transport(self, operation, expected_project):
        self.dispatch_calls.append((operation, expected_project))
        if isinstance(self.action, Exception):
            raise self.action
        return dict(self.action)


class StubMacOSBackend(adapter_module.MacOSAccessibilityBackend):
    def __init__(self, native_bridge, legacy_state=None):
        super().__init__(10.0, native_bridge_factory=native_bridge)
        self.legacy_state = legacy_state
        self.jxa_calls = []

    def _require_macos(self):
        return None

    def _screen_unlocked(self):
        return True

    def _run_jxa(self, source, *, may_dispatch=False):
        self.jxa_calls.append((source, may_dispatch))
        if "process_identifier" in source and "discoverProcessWindows" not in source:
            return {
                "ok": True,
                "logic_running": True,
                "bundle_identifier": "com.apple.mobilelogic",
                "process_identifier": 4242,
                "frontmost": False,
            }
        if self.legacy_state is not None:
            state = json.loads(json.dumps(self.legacy_state))
            if "const INCLUDE_CONTROLS = false;" in source:
                state["controls"] = []
                state["transport_controls_observed"] = False
                state["transport_controls_complete"] = False
            return state
        raise AssertionError("legacy JXA snapshot should not be called")


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
        self.assertIsNone(result["data"]["window_discovery_diagnostic"])
        self.assertTrue(result["data"]["window_set_complete"])
        self.assertFalse(result["data"]["focus_temporarily_changed"])
        self.assertTrue(result["data"]["transport_controls_observed"])
        self.assertTrue(result["data"]["transport_controls_complete"])
        self.assertIn("transport.play", result["data"]["capabilities"])
        self.assertIn("transport.stop", result["data"]["capabilities"])

    def test_app_status_defers_transport_control_traversal(self):
        backend = FakeBackend(snapshot(self.project))
        result = adapter_module.ReferenceAdapter(backend).observe("app.status")
        self.assertEqual(backend.snapshot_calls, [False])
        self.assertFalse(result["data"]["transport_controls_observed"])
        self.assertFalse(result["data"]["transport_controls_complete"])
        self.assertEqual(result["data"]["capabilities"], ["app.status", "project.current"])

    def test_project_current_defers_transport_control_traversal(self):
        backend = FakeBackend(snapshot(self.project))
        result = adapter_module.ReferenceAdapter(backend).observe("project.current")
        self.assertEqual(backend.snapshot_calls, [False])
        self.assertFalse(result["data"]["transport_controls_observed"])
        self.assertEqual(result["data"]["current_project"], str(Path(self.project).resolve()))

    def test_project_identity_reports_direct_ax_fallback_source(self):
        state = snapshot(self.project)
        state["windows"][0]["document_source"] = "application.AXDocument"
        state["windows"][0]["title_source"] = "AXTitleUIElement"
        observed = adapter_module.ReferenceAdapter(FakeBackend(state)).observe("project.current")
        self.assertEqual(observed["data"]["project_identity_source"], "application.AXDocument")

        state["windows"][0]["document"] = None
        observed = adapter_module.ReferenceAdapter(FakeBackend(state)).observe("project.current")
        self.assertEqual(observed["data"]["project_identity_source"], "AXTitleUIElement")

    def test_snapshot_jxa_gates_control_traversal(self):
        lightweight_source = adapter_module.render_jxa(
            adapter_module.SNAPSHOT_JXA,
            {"INCLUDE_CONTROLS": False},
        )
        self.assertIn("const INCLUDE_CONTROLS = false;", lightweight_source)
        self.assertIn("mainWindow !== null && INCLUDE_CONTROLS", lightweight_source)
        self.assertIn("transport_controls_observed: controlsObserved", lightweight_source)
        self.assertIn("boundedDescendants(mainWindow, 4000, 32)", lightweight_source)
        self.assertIn("transport_controls_complete: controlsObserved && !controlsTruncated", lightweight_source)
        self.assertNotIn("entireContents()", lightweight_source)

    def test_native_ax_snapshot_is_primary_and_does_not_foreground_logic(self):
        native_state = snapshot(
            self.project,
            window_discovery_source="AXUIElement.AXWindows",
        )
        native_state["focus_temporarily_changed"] = False
        native = FakeNativeBridge(native_state)
        backend = StubMacOSBackend(native)
        observed = backend.snapshot(include_controls=True)
        self.assertEqual(observed["accessibility_backend"], "AXUIElement")
        self.assertEqual(observed["process_identifier"], 4242)
        self.assertFalse(observed["frontmost"])
        self.assertFalse(observed["focus_temporarily_changed"])
        self.assertEqual(native.snapshot_calls, [(True, False)])
        self.assertEqual(len(backend.jxa_calls), 1)
        self.assertNotIn("discoverProcessWindows", backend.jxa_calls[0][0])

    def test_untrusted_native_ax_falls_back_to_system_events(self):
        native_state = {
            "accessibility_authorized": False,
            "window_discovery_source": "AXUIElement",
            "window_discovery_diagnostic": "native_ax_client_not_trusted",
            "window_set_complete": False,
            "transport_controls_observed": False,
            "transport_controls_complete": False,
            "windows": [],
            "controls": [],
        }
        legacy = snapshot(self.project, window_discovery_source="process.windows")
        native = FakeNativeBridge(native_state, trusted=False)
        backend = StubMacOSBackend(native, legacy)
        observed = backend.snapshot(include_controls=True)
        self.assertEqual(observed["accessibility_backend"], "SystemEvents")
        self.assertEqual(observed["window_discovery_source"], "process.windows")
        self.assertEqual(observed["native_accessibility_diagnostic"], "native_ax_client_not_trusted")
        self.assertEqual(len(backend.jxa_calls), 2)

    def test_native_ax_bridge_error_falls_back_with_diagnostic(self):
        def unavailable_bridge(process_identifier, timeout_seconds):
            raise ValueError(f"cannot bind {process_identifier} within {timeout_seconds}")

        legacy = snapshot(self.project, window_discovery_source="process.windows")
        backend = StubMacOSBackend(unavailable_bridge, legacy)
        observed = backend.snapshot(include_controls=True)
        self.assertEqual(observed["accessibility_backend"], "SystemEvents")
        self.assertEqual(observed["native_accessibility_diagnostic"], "native_ax_bridge_error:ValueError")
        self.assertEqual(observed["control_tree_diagnostic"], "native_ax_bridge_error:ValueError")
        self.assertFalse(observed["transport_controls_observed"])
        self.assertFalse(observed["transport_controls_complete"])
        self.assertNotIn("transport.state", adapter_module.runtime_capabilities(observed))
        self.assertIn("const INCLUDE_CONTROLS = false;", backend.jxa_calls[1][0])

    def test_native_ax_dispatch_rebinds_the_same_process_before_action(self):
        native_state = snapshot(self.project, window_discovery_source="AXUIElement.AXWindows")
        native = FakeNativeBridge(
            native_state,
            action={
                "ok": True,
                "performed": True,
                "already_satisfied": False,
                "window_discovery_source": "AXUIElement.AXWindows",
            },
        )
        backend = StubMacOSBackend(native)
        result = backend.dispatch_transport(
            "transport.play",
            self.project,
            "com.apple.mobilelogic",
            4242,
        )
        self.assertTrue(result["performed"])
        self.assertEqual(native.dispatch_calls, [("transport.play", self.project)])
        self.assertEqual(native.factory_calls, [(4242, 10.0)])

    def test_native_ax_dispatch_type_error_stops_before_legacy_action(self):
        def invalid_bridge(process_identifier, timeout_seconds):
            raise TypeError(f"invalid pointer for {process_identifier} within {timeout_seconds}")

        backend = StubMacOSBackend(invalid_bridge, snapshot(self.project))
        with self.assertRaises(adapter_module.AdapterFailure) as caught:
            backend.dispatch_transport(
                "transport.play",
                self.project,
                "com.apple.mobilelogic",
                4242,
            )
        self.assertEqual(caught.exception.kind, "native_ax_bridge_failed")
        self.assertTrue(caught.exception.definitive)
        self.assertEqual(len(backend.jxa_calls), 1)

    def test_native_ax_action_exception_is_unknown_and_never_retried(self):
        for exception in (
            AttributeError("native action response is unavailable"),
            OSError("native action connection failed"),
        ):
            with self.subTest(exception=type(exception).__name__):
                native = FakeNativeBridge(
                    snapshot(self.project, window_discovery_source="AXUIElement.AXWindows"),
                    action=exception,
                )
                backend = StubMacOSBackend(native, snapshot(self.project))
                with self.assertRaises(adapter_module.AdapterFailure) as caught:
                    backend.dispatch_transport(
                        "transport.play",
                        self.project,
                        "com.apple.mobilelogic",
                        4242,
                    )
                self.assertEqual(caught.exception.kind, "native_ax_bridge_failed")
                self.assertFalse(caught.exception.definitive)
                self.assertTrue(caught.exception.may_have_dispatched)
                self.assertEqual(native.dispatch_calls, [("transport.play", self.project)])
                self.assertEqual(len(backend.jxa_calls), 1)

    def test_native_ax_context_exit_exception_after_action_is_unknown(self):
        native = FakeNativeBridge(
            snapshot(self.project, window_discovery_source="AXUIElement.AXWindows"),
            exit_error=TypeError("native bridge cleanup failed"),
        )
        backend = StubMacOSBackend(native, snapshot(self.project))
        with self.assertRaises(adapter_module.AdapterFailure) as caught:
            backend.dispatch_transport(
                "transport.play",
                self.project,
                "com.apple.mobilelogic",
                4242,
            )
        self.assertEqual(caught.exception.kind, "native_ax_bridge_failed")
        self.assertFalse(caught.exception.definitive)
        self.assertTrue(caught.exception.may_have_dispatched)
        self.assertEqual(native.dispatch_calls, [("transport.play", self.project)])
        self.assertEqual(len(backend.jxa_calls), 1)

    def test_native_ax_source_is_bounded_and_does_not_activate_logic(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("AXUIElementSetMessagingTimeout", source)
        self.assertIn("AXUIElementCopyAttributeValues", source)
        self.assertIn("MAX_WINDOWS = 64", source)
        self.assertIn("MAX_CONTROLS = 4000", source)
        self.assertIn("MAX_DEPTH = 32", source)
        self.assertIn("native_ax_main_window_unavailable", source)
        self.assertNotIn("strict=True", source)
        self.assertNotIn("process.frontmost = true", adapter_module.PROCESS_SNAPSHOT_JXA)
        self.assertEqual(adapter_module.ax_error_name(-25204), "cannot_complete")

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
        self.assertEqual(dispatched["error"]["kind"], "project_identity_unavailable")

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

    def test_frontmost_retry_complete_snapshot_restores_full_runtime_capabilities(self):
        state = snapshot(
            self.project,
            window_discovery_source="frontmost-retry.process.windows",
            window_set_complete=True,
        )
        state["frontmost"] = False
        state["focus_temporarily_changed"] = True
        adapter = adapter_module.ReferenceAdapter(FakeBackend(state))
        observed = adapter.observe("transport.state")
        self.assertFalse(observed["data"]["window_discovery_diagnostic"])
        self.assertTrue(observed["data"]["focus_temporarily_changed"])
        self.assertTrue(observed["data"]["window_set_complete"])
        self.assertEqual(observed["data"]["current_project"], str(Path(self.project).resolve()))
        self.assertIn("transport.play", observed["data"]["capabilities"])

    def test_incomplete_control_tree_withholds_state_and_writes(self):
        state = snapshot(self.project)
        state["controls_truncated"] = True
        state["transport_controls_complete"] = False
        backend = FakeBackend(state)
        adapter = adapter_module.ReferenceAdapter(backend)
        observed = adapter.observe("transport.state")
        self.assertTrue(observed["data"]["transport_controls_observed"])
        self.assertFalse(observed["data"]["transport_controls_complete"])
        self.assertNotIn("transport.state", observed["data"]["capabilities"])
        dispatched = adapter.dispatch(preflight("transport.play", self.project))
        self.assertEqual(dispatched["error"]["kind"], "transport_control_tree_truncated")
        self.assertEqual(backend.dispatch_calls, [])

    def test_missing_main_window_identity_reports_actionable_cause(self):
        state = snapshot(
            self.project,
            window_discovery_source="AXMainWindow",
            window_set_complete=False,
        )
        state["window_discovery_diagnostic"] = "frontmost_retry_no_complete_window_set"
        state["windows"][0]["title"] = None
        state["windows"][0]["document"] = None
        observed = adapter_module.ReferenceAdapter(FakeBackend(state)).observe("project.current")
        self.assertTrue(observed["data"]["current_project_unavailable"])
        self.assertEqual(
            observed["data"]["current_project_unavailable_reason"],
            "main_window_has_no_document_or_title",
        )
        self.assertIsNone(observed["data"]["project_identity_source"])
        self.assertEqual(
            observed["data"]["window_discovery_diagnostic"],
            "frontmost_retry_no_complete_window_set",
        )

    def test_complete_window_without_project_identity_has_specific_dispatch_failure(self):
        state = snapshot(self.project)
        state["windows"][0]["title"] = None
        state["windows"][0]["document"] = None
        backend = FakeBackend(state)
        dispatched = adapter_module.ReferenceAdapter(backend).dispatch(preflight("transport.play", self.project))
        self.assertEqual(dispatched["error"]["kind"], "project_identity_unavailable")
        self.assertIn("main_window_has_no_document_or_title", dispatched["error"]["message"])
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
        self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, "com.apple.mobilelogic", 4242)])

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
        self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, "com.apple.mobilelogic", 4242)])

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
                self.assertEqual(backend.dispatch_calls, [("transport.play", self.project, bundle_identifier, 4242)])

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
            "frontmost-retry.",
            "AXMainWindow",
            "AXFocusedWindow",
        ):
            self.assertIn(source, snapshot_source)
            self.assertIn(source, action_source)
        self.assertIn("discoverProcessWindows(systemEvents, process)", snapshot_source)
        self.assertIn("discoverProcessWindows(systemEvents, process)", action_source)
        self.assertIn("restoreWindowDiscoveryFocus(windowDiscovery)", snapshot_source)
        self.assertIn("restoreWindowDiscoveryFocus(windowDiscovery)", action_source)
        self.assertIn("process.frontmost = true", adapter_module.WINDOW_DISCOVERY_JXA)
        self.assertNotIn("entireContents()", snapshot_source)
        self.assertNotIn("entireContents()", action_source)
        self.assertIn('reason: "window_set_incomplete"', action_source)
        self.assertIn('reason: "transport_control_tree_truncated"', action_source)

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
