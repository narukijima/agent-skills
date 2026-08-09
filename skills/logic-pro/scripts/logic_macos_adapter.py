#!/usr/bin/env python3
"""Reference macOS Accessibility adapter for the Logic Pro skill.

The adapter deliberately exposes only operations that can be identified and
verified through Logic Pro's Accessibility tree without coordinates or toggle
key commands. Dispatch never includes readback: callers must issue a new
``observe`` command after the action and pass that evidence to logic_guard.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ADAPTER_NAME = "logic-pro-macos-accessibility"
ADAPTER_VERSION = "0.1.1"
SUPPORTED_LOGIC_BUNDLE_IDS = ("com.apple.mobilelogic", "com.apple.logic10")
EVIDENCE_SOURCE = "logic-accessibility"

READ_OPERATIONS = (
    "app.status",
    "project.current",
    "transport.state",
    "tracks.list",
    "track.selected",
    "regions.list",
    "instruments.list",
    "midi.ports",
)
WRITE_OPERATIONS = (
    "transport.play",
    "transport.stop",
    "transport.set_tempo",
    "transport.set_position",
    "track.select",
    "track.set_instrument",
    "midi.import_file",
    "project.save",
    "project.save_as",
    "project.bounce",
)
ALL_OPERATIONS = READ_OPERATIONS + WRITE_OPERATIONS
IMPLEMENTED_READS = ("app.status", "project.current", "transport.state")
IMPLEMENTED_WRITES = ("transport.play", "transport.stop")

PLAY_LABELS = ("play", "再生")
STOP_LABELS = ("stop", "停止")
BEGINNING_LABELS = ("go to beginning", "先頭へ移動", "先頭に移動")


BUNDLE_DISCOVERY_JXA = r'''
function findLogicProcess(systemEvents, preferredBundleId) {
  const bundleIds = preferredBundleId === null ? SUPPORTED_LOGIC_BUNDLE_IDS : [preferredBundleId];
  for (let i = 0; i < bundleIds.length; i++) {
    let processes = [];
    try { processes = systemEvents.applicationProcesses.whose({bundleIdentifier: bundleIds[i]})(); } catch (_) { processes = []; }
    if (processes && processes.length > 0) return {process: processes[0], bundle_identifier: bundleIds[i]};
  }
  return null;
}
'''


SNAPSHOT_JXA = r'''
function run() {
  function safe(fn, fallback) { try { const value = fn(); return value === undefined ? fallback : value; } catch (_) { return fallback; } }
  function text(value) {
    if (value === null || value === undefined) return null;
    try { return String(value); } catch (_) { return null; }
  }
  function attr(element, name) {
    const attrs = safe(() => element.attributes.whose({name: name})(), []);
    if (!attrs || attrs.length === 0) return null;
    return safe(() => attrs[0].value(), null);
  }
  const systemEvents = Application("/System/Library/CoreServices/System Events.app");
  const discovered = findLogicProcess(systemEvents, null);
  if (discovered === null) {
    return JSON.stringify({ok: true, logic_running: false, bundle_identifier: null, accessibility_authorized: false, windows: [], controls: []});
  }
  const process = discovered.process;
  const windows = safe(() => process.windows(), null);
  if (windows === null) {
    return JSON.stringify({ok: true, logic_running: true, bundle_identifier: discovered.bundle_identifier, accessibility_authorized: false, windows: [], controls: []});
  }
  let mainWindow = null;
  for (let i = 0; i < windows.length; i++) {
    if (safe(() => Boolean(attr(windows[i], "AXMain")), false)) { mainWindow = windows[i]; break; }
  }
  if (mainWindow === null && windows.length > 0) mainWindow = windows[0];
  const windowRows = [];
  let modal = false;
  for (let i = 0; i < windows.length; i++) {
    const subrole = text(safe(() => windows[i].subrole(), null));
    const sheets = safe(() => windows[i].sheets(), []);
    if (subrole === "AXDialog" || (sheets && sheets.length > 0)) modal = true;
    windowRows.push({
      title: text(safe(() => windows[i].name(), null)),
      role: text(safe(() => windows[i].role(), null)),
      subrole: subrole,
      document: text(attr(windows[i], "AXDocument")),
      main: Boolean(attr(windows[i], "AXMain")),
      sheet_count: sheets ? sheets.length : 0
    });
  }
  const controls = [];
  if (mainWindow !== null) {
    const contents = safe(() => mainWindow.entireContents(), []);
    const bounded = contents.slice(0, 4000);
    for (let i = 0; i < bounded.length; i++) {
      const element = bounded[i];
      const role = text(safe(() => element.role(), null));
      if (!["AXButton", "AXCheckBox", "AXRadioButton", "AXTextField", "AXStaticText"].includes(role)) continue;
      const actions = safe(() => element.actions(), []);
      controls.push({
        role: role,
        title: text(safe(() => element.name(), null)),
        description: text(safe(() => element.description(), null)),
        help: text(safe(() => element.help(), null)),
        identifier: text(attr(element, "AXIdentifier")),
        value: safe(() => element.value(), null),
        enabled: safe(() => Boolean(element.enabled()), null),
        actions: actions.map(action => text(safe(() => action.name(), null))).filter(Boolean)
      });
    }
  }
  return JSON.stringify({
    ok: true,
    logic_running: true,
    bundle_identifier: discovered.bundle_identifier,
    accessibility_authorized: true,
    frontmost: safe(() => Boolean(process.frontmost()), null),
    modal_dialog: modal,
    windows: windowRows,
    controls: controls,
    controls_truncated: mainWindow !== null && safe(() => mainWindow.entireContents().length, 0) > 4000
  });
}
'''


ACTION_JXA = r'''
function run() {
  function safe(fn, fallback) { try { const value = fn(); return value === undefined ? fallback : value; } catch (_) { return fallback; } }
  function text(value) { if (value === null || value === undefined) return null; try { return String(value); } catch (_) { return null; } }
  function attr(element, name) {
    const attrs = safe(() => element.attributes.whose({name: name})(), []);
    if (!attrs || attrs.length === 0) return null;
    return safe(() => attrs[0].value(), null);
  }
  function normalized(value) { return text(value) === null ? "" : text(value).trim().toLocaleLowerCase(); }
  function labels(element) {
    return [safe(() => element.name(), null), safe(() => element.description(), null), safe(() => element.help(), null), attr(element, "AXIdentifier")]
      .map(normalized).filter(value => value.length > 0);
  }
  function matches(element, candidates) {
    const values = labels(element);
    return values.some(value => candidates.some(candidate => value === candidate || value === candidate + " button" || value === candidate + "ボタン"));
  }
  function truth(value) {
    if (value === true || value === 1) return true;
    const candidate = normalized(value);
    if (["1", "true", "yes", "on", "selected"].includes(candidate)) return true;
    if (["0", "false", "no", "off", "not selected"].includes(candidate)) return false;
    return null;
  }
  function basename(path) {
    if (!path) return null;
    let value = String(path);
    try { value = decodeURIComponent(value); } catch (_) {}
    value = value.replace(/^file:\/\//, "").replace(/\/$/, "");
    const parts = value.split("/");
    return parts[parts.length - 1];
  }
  const systemEvents = Application("/System/Library/CoreServices/System Events.app");
  const discovered = findLogicProcess(systemEvents, EXPECTED_BUNDLE_ID);
  if (discovered === null) return JSON.stringify({ok: false, definitive: true, reason: "logic_not_running"});
  const process = discovered.process;
  const windows = safe(() => process.windows(), null);
  if (windows === null) return JSON.stringify({ok: false, definitive: true, reason: "accessibility_not_authorized"});
  let mainWindow = null;
  for (let i = 0; i < windows.length; i++) {
    const sheets = safe(() => windows[i].sheets(), []);
    const subrole = text(safe(() => windows[i].subrole(), null));
    if (subrole === "AXDialog" || (sheets && sheets.length > 0)) return JSON.stringify({ok: false, definitive: true, reason: "modal_dialog_present"});
    if (mainWindow === null && Boolean(attr(windows[i], "AXMain"))) mainWindow = windows[i];
  }
  if (mainWindow === null && windows.length > 0) mainWindow = windows[0];
  if (mainWindow === null) return JSON.stringify({ok: false, definitive: true, reason: "project_window_not_found"});
  const expectedName = basename(EXPECTED_PROJECT);
  const documentValue = text(attr(mainWindow, "AXDocument"));
  const windowTitle = text(safe(() => mainWindow.name(), null));
  if (documentValue) {
    if (basename(documentValue) !== expectedName) return JSON.stringify({ok: false, definitive: true, reason: "project_mismatch"});
  } else if (windowTitle !== expectedName) {
    return JSON.stringify({ok: false, definitive: true, reason: "project_mismatch"});
  }
  const contents = safe(() => mainWindow.entireContents(), []).slice(0, 4000);
  const buttons = contents.filter(element => text(safe(() => element.role(), null)) === "AXButton");
  const playLabels = ["play", "再生"];
  const stopLabels = ["stop", "停止"];
  const beginningLabels = ["go to beginning", "先頭へ移動", "先頭に移動"];
  const play = buttons.find(element => matches(element, playLabels));
  const stop = buttons.find(element => matches(element, stopLabels));
  const beginning = buttons.find(element => matches(element, beginningLabels));
  let playing = play ? truth(safe(() => play.value(), null)) : null;
  if (playing === null && stop && !beginning) playing = true;
  if (playing === null && beginning && !stop) playing = false;
  if (playing === null) return JSON.stringify({ok: false, definitive: true, reason: "transport_state_not_observable"});
  const wantsPlaying = REQUESTED_OPERATION === "transport.play";
  if (playing === wantsPlaying) return JSON.stringify({ok: true, performed: false, already_satisfied: true, before: {is_playing: playing}});
  const target = wantsPlaying ? play : stop;
  if (!target) return JSON.stringify({ok: false, definitive: true, reason: wantsPlaying ? "play_control_not_found" : "stop_control_not_found"});
  const actions = safe(() => target.actions(), []);
  const press = actions.find(action => text(safe(() => action.name(), null)) === "AXPress");
  if (!press) return JSON.stringify({ok: false, definitive: true, reason: "ax_press_not_supported"});
  press.perform();
  return JSON.stringify({ok: true, performed: true, already_satisfied: false, bundle_identifier: discovered.bundle_identifier, before: {is_playing: playing}});
}
'''


class AdapterFailure(Exception):
    def __init__(self, kind: str, message: str, *, definitive: bool, may_have_dispatched: bool = False):
        super().__init__(message)
        self.kind = kind
        self.definitive = definitive
        self.may_have_dispatched = may_have_dispatched


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def render_jxa(source: str, constants: dict[str, Any] | None = None) -> str:
    assignments = {
        "SUPPORTED_LOGIC_BUNDLE_IDS": list(SUPPORTED_LOGIC_BUNDLE_IDS),
        **(constants or {}),
    }
    prefix = "".join(f"const {name} = {json.dumps(value)};\n" for name, value in assignments.items())
    return prefix + BUNDLE_DISCOVERY_JXA + source


def normalize_document_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
    if not os.path.isabs(value):
        return None
    return os.path.realpath(os.path.abspath(value))


def element_text(control: dict[str, Any]) -> list[str]:
    values = []
    for key in ("title", "description", "help", "identifier"):
        value = control.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().casefold())
    return values


def matches_label(control: dict[str, Any], labels: tuple[str, ...]) -> bool:
    candidates = tuple(label.casefold() for label in labels)
    for value in element_text(control):
        if any(value in {candidate, candidate + " button", candidate + "ボタン"} for candidate in candidates):
            return True
    return False


def boolean_value(value: Any) -> bool | None:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on", "selected"}:
            return True
        if normalized in {"0", "false", "no", "off", "not selected"}:
            return False
    return None


def transport_from_controls(controls: Any) -> dict[str, Any]:
    if not isinstance(controls, list):
        controls = []
    buttons = [control for control in controls if isinstance(control, dict) and control.get("role") == "AXButton"]
    play = next((control for control in buttons if matches_label(control, PLAY_LABELS)), None)
    stop = next((control for control in buttons if matches_label(control, STOP_LABELS)), None)
    beginning = next((control for control in buttons if matches_label(control, BEGINNING_LABELS)), None)
    playing = boolean_value(play.get("value")) if play else None
    state_basis = "play-control-value" if playing is not None else None
    if playing is None and stop is not None and beginning is None:
        playing = True
        state_basis = "stop-control-present"
    elif playing is None and beginning is not None and stop is None:
        playing = False
        state_basis = "go-to-beginning-control-present"
    return {
        "is_playing": playing,
        "state_basis": state_basis,
        "play_control_available": play is not None and "AXPress" in play.get("actions", []),
        "stop_control_available": stop is not None and "AXPress" in stop.get("actions", []),
    }


def main_window(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    windows = snapshot.get("windows")
    if not isinstance(windows, list):
        return None
    for window in windows:
        if isinstance(window, dict) and window.get("main") is True:
            return window
    return next((window for window in windows if isinstance(window, dict)), None)


def project_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    window = main_window(snapshot)
    if window is None:
        return {"current_project": None, "current_project_unavailable": True, "window_project_name": None}
    current = normalize_document_path(window.get("document"))
    return {
        "current_project": current,
        "current_project_unavailable": current is None,
        "window_project_name": window.get("title") if isinstance(window.get("title"), str) else None,
    }


def runtime_capabilities(snapshot: dict[str, Any]) -> list[str]:
    capabilities = ["app.status"]
    if snapshot.get("logic_running") is not True or snapshot.get("accessibility_authorized") is not True:
        return capabilities
    if main_window(snapshot) is not None:
        capabilities.append("project.current")
    transport = transport_from_controls(snapshot.get("controls"))
    if transport["is_playing"] is not None:
        capabilities.append("transport.state")
        if transport["is_playing"] is True or transport["play_control_available"]:
            capabilities.append("transport.play")
        if transport["is_playing"] is False or transport["stop_control_available"]:
            capabilities.append("transport.stop")
    return [operation for operation in ALL_OPERATIONS if operation in capabilities]


def validate_preflight(preflight: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(preflight, dict):
        raise AdapterFailure("invalid_preflight", "preflight must be a JSON object", definitive=True)
    if preflight.get("ok") is not True or preflight.get("classification") != "authorized":
        raise AdapterFailure("invalid_preflight", "preflight is not authorized", definitive=True)
    if preflight.get("impact") != "write":
        raise AdapterFailure("invalid_preflight", "dispatch requires a write preflight", definitive=True)
    request = preflight.get("request")
    if not isinstance(request, dict):
        raise AdapterFailure("invalid_preflight", "preflight.request must be an object", definitive=True)
    operation = request.get("operation")
    if operation not in WRITE_OPERATIONS or preflight.get("operation") != operation:
        raise AdapterFailure("invalid_preflight", "preflight operation is not a matching allowlisted write", definitive=True)
    expected_hash = hashlib.sha256(canonical_json(request)).hexdigest()
    if preflight.get("request_sha256") != expected_hash:
        raise AdapterFailure("invalid_preflight", "preflight request fingerprint does not match", definitive=True)
    if not isinstance(request.get("arguments"), dict):
        raise AdapterFailure("invalid_preflight", "preflight request arguments must be an object", definitive=True)
    expected_project = request.get("expected_project")
    if not isinstance(expected_project, str) or not os.path.isabs(expected_project):
        raise AdapterFailure("invalid_preflight", "preflight expected_project must be an absolute path", definitive=True)
    return operation, request


def assert_project_binding(snapshot: dict[str, Any], expected_project: str) -> None:
    project = project_from_snapshot(snapshot)
    expected = os.path.realpath(os.path.abspath(expected_project))
    current = project["current_project"]
    if current is not None:
        if current != expected:
            raise AdapterFailure("project_mismatch", "current Logic project does not match preflight", definitive=True)
        return
    if project["window_project_name"] != Path(expected).name:
        raise AdapterFailure("project_mismatch", "Logic window title does not exactly match expected .logicx name", definitive=True)


class MacOSAccessibilityBackend:
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def _require_macos(self) -> None:
        if platform.system() != "Darwin":
            raise AdapterFailure("platform_unsupported", "the reference adapter requires macOS", definitive=True)

    def _run_jxa(self, source: str, *, may_dispatch: bool = False) -> dict[str, Any]:
        self._require_macos()
        try:
            completed = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", "-"],
                input=source,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterFailure(
                "timeout",
                f"Accessibility request timed out after {self.timeout_seconds:g} seconds",
                definitive=False,
                may_have_dispatched=may_dispatch,
            ) from exc
        except OSError as exc:
            raise AdapterFailure("adapter_unavailable", str(exc), definitive=True) from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "osascript failed without an error message"
            raise AdapterFailure(
                "accessibility_error",
                detail,
                definitive=not may_dispatch,
                may_have_dispatched=may_dispatch,
            )
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterFailure(
                "invalid_adapter_response",
                "Accessibility bridge returned invalid JSON",
                definitive=not may_dispatch,
                may_have_dispatched=may_dispatch,
            ) from exc
        if not isinstance(value, dict):
            raise AdapterFailure("invalid_adapter_response", "Accessibility bridge returned a non-object", definitive=True)
        return value

    def _screen_unlocked(self) -> bool | None:
        self._require_macos()
        try:
            completed = subprocess.run(
                ["/usr/sbin/ioreg", "-n", "Root", "-d1"],
                text=True,
                capture_output=True,
                timeout=min(self.timeout_seconds, 3.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        output = completed.stdout
        if '"CGSSessionScreenIsLocked" = Yes' in output or '"CGSSessionScreenIsLocked" = true' in output:
            return False
        if '"CGSSessionScreenIsLocked" = No' in output or '"CGSSessionScreenIsLocked" = false' in output:
            return True
        if '"kCGSSessionOnConsoleKey"=Yes' in output and '"kCGSessionLoginDoneKey"=Yes' in output:
            return True
        return None

    def snapshot(self) -> dict[str, Any]:
        snapshot = self._run_jxa(render_jxa(SNAPSHOT_JXA))
        snapshot["screen_unlocked"] = self._screen_unlocked()
        return snapshot

    def dispatch_transport(self, operation: str, expected_project: str, bundle_identifier: str) -> dict[str, Any]:
        if bundle_identifier not in SUPPORTED_LOGIC_BUNDLE_IDS:
            raise AdapterFailure("unsupported_logic_bundle", "observed Logic bundle identifier is unsupported", definitive=True)
        source = render_jxa(
            ACTION_JXA,
            {
                "REQUESTED_OPERATION": operation,
                "EXPECTED_PROJECT": expected_project,
                "EXPECTED_BUNDLE_ID": bundle_identifier,
            },
        )
        result = self._run_jxa(source, may_dispatch=True)
        if result.get("ok") is not True:
            definitive = result.get("definitive") is True
            raise AdapterFailure(
                str(result.get("reason") or "dispatch_failed"),
                str(result.get("reason") or "Logic rejected the operation"),
                definitive=definitive,
                may_have_dispatched=not definitive,
            )
        return result


class ReferenceAdapter:
    def __init__(self, backend: Any):
        self.backend = backend

    def capability_document(self) -> dict[str, Any]:
        operations = []
        for operation in ALL_OPERATIONS:
            implemented = operation in IMPLEMENTED_READS + IMPLEMENTED_WRITES
            operations.append(
                {
                    "operation": operation,
                    "support": "implemented" if implemented else "not-implemented",
                    "reason": None if implemented else "no stable independent Accessibility readback in reference profile",
                }
            )
        return {
            "ok": True,
            "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
            "platform": {"required": "macOS", "current": platform.system()},
            "supported_bundle_identifiers": list(SUPPORTED_LOGIC_BUNDLE_IDS),
            "capabilities": list(IMPLEMENTED_READS + IMPLEMENTED_WRITES),
            "operations": operations,
            "ui_languages": ["en", "ja"],
            "dispatch_contract": "one-semantic-operation",
            "readback_contract": "separate-observe-required",
        }

    def observe(self, operation: str) -> dict[str, Any]:
        if operation not in IMPLEMENTED_READS:
            raise AdapterFailure("unsupported_operation", f"reference adapter does not implement read: {operation}", definitive=True)
        snapshot = self.backend.snapshot()
        project = project_from_snapshot(snapshot)
        transport = transport_from_controls(snapshot.get("controls"))
        common = {
            "fresh": True,
            "observed_at": utc_now(),
            "source": EVIDENCE_SOURCE,
            "operation": operation,
        }
        if operation == "app.status":
            data = {
                "logic_running": snapshot.get("logic_running") is True,
                "bundle_identifier": snapshot.get("bundle_identifier"),
                "screen_unlocked": snapshot.get("screen_unlocked"),
                "accessibility_authorized": snapshot.get("accessibility_authorized") is True,
                "modal_dialog": snapshot.get("modal_dialog") if snapshot.get("logic_running") is True else None,
                "frontmost": snapshot.get("frontmost"),
                "capabilities": runtime_capabilities(snapshot),
            }
        elif operation == "project.current":
            data = project
            data["bundle_identifier"] = snapshot.get("bundle_identifier")
            data["capabilities"] = runtime_capabilities(snapshot)
        else:
            data = transport
            data.update(project)
            data["bundle_identifier"] = snapshot.get("bundle_identifier")
            data["capabilities"] = runtime_capabilities(snapshot)
        return {"ok": True, **common, "data": data}

    def dispatch(self, preflight: Any) -> dict[str, Any]:
        try:
            operation, request = validate_preflight(preflight)
            if operation not in IMPLEMENTED_WRITES:
                raise AdapterFailure("unsupported_operation", f"reference adapter does not implement write: {operation}", definitive=True)
            snapshot = self.backend.snapshot()
            if snapshot.get("logic_running") is not True:
                raise AdapterFailure("logic_not_running", "Logic Pro is not running", definitive=True)
            if snapshot.get("screen_unlocked") is not True:
                raise AdapterFailure("screen_locked_or_unknown", "screen unlocked state is not confirmed", definitive=True)
            if snapshot.get("accessibility_authorized") is not True:
                raise AdapterFailure("accessibility_not_authorized", "Accessibility permission is not confirmed", definitive=True)
            if snapshot.get("modal_dialog") is not False:
                raise AdapterFailure("modal_dialog_present", "Logic modal state is unsafe or unknown", definitive=True)
            bundle_identifier = snapshot.get("bundle_identifier")
            if bundle_identifier not in SUPPORTED_LOGIC_BUNDLE_IDS:
                raise AdapterFailure("unsupported_logic_bundle", "running Logic bundle identifier is unsupported", definitive=True)
            expected_project = request["expected_project"]
            assert_project_binding(snapshot, expected_project)
            capabilities = runtime_capabilities(snapshot)
            if operation not in capabilities or "transport.state" not in capabilities:
                raise AdapterFailure("capability_unavailable", "operation or independent readback is unavailable", definitive=True)
            action = self.backend.dispatch_transport(operation, expected_project, bundle_identifier)
            return {
                "ok": True,
                "operation": operation,
                "dispatch": {
                    "status": "success",
                    "definitive": True,
                    "performed": action.get("performed") is True,
                    "already_satisfied": action.get("already_satisfied") is True,
                    "adapter": ADAPTER_NAME,
                    "adapter_version": ADAPTER_VERSION,
                    "bundle_identifier": bundle_identifier,
                },
                "readback_required": True,
            }
        except AdapterFailure as exc:
            unknown = not exc.definitive or exc.may_have_dispatched
            return {
                "ok": False,
                "operation": preflight.get("operation") if isinstance(preflight, dict) else None,
                "dispatch": {"status": "unknown" if unknown else "failed", "definitive": not unknown},
                "error": {"kind": exc.kind, "message": str(exc)},
                "readback_required": unknown,
            }


def read_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def emit(value: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reference macOS Accessibility adapter for Logic Pro.")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--timeout", type=float, default=10.0, help="Accessibility request timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities", help="list the static semantic operation support matrix")
    observe = subparsers.add_parser("observe", help="perform one fresh read from Logic Pro")
    observe.add_argument("--operation", required=True, choices=READ_OPERATIONS)
    dispatch = subparsers.add_parser("dispatch", help="dispatch one guard-authorized semantic write")
    dispatch.add_argument("--preflight", required=True, help="logic_guard.py preflight JSON, or - for stdin")
    return parser


def main(argv: list[str] | None = None, backend: Any = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        emit({"ok": False, "error": {"kind": "invalid_timeout", "message": "timeout must be greater than zero"}}, args.pretty)
        return 2
    adapter = ReferenceAdapter(backend or MacOSAccessibilityBackend(args.timeout))
    try:
        if args.command == "capabilities":
            output = adapter.capability_document()
        elif args.command == "observe":
            output = adapter.observe(args.operation)
        else:
            output = adapter.dispatch(read_json(args.preflight))
        emit(output, args.pretty)
        if output.get("ok") is True:
            return 0
        return 3 if output.get("dispatch", {}).get("status") == "unknown" else 2
    except (AdapterFailure, json.JSONDecodeError, OSError) as exc:
        if isinstance(exc, AdapterFailure):
            error = {"kind": exc.kind, "message": str(exc)}
        else:
            error = {"kind": "invalid_input", "message": str(exc)}
        emit({"ok": False, "error": error}, args.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
