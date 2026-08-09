#!/usr/bin/env python3
"""Reference macOS Accessibility adapter for the Logic Pro skill.

The adapter deliberately exposes only operations that can be identified and
verified through Logic Pro's Accessibility tree without coordinates or toggle
key commands. Dispatch never includes readback: callers must issue a new
``observe`` command after the action and pass that evidence to logic_guard.py.
"""

from __future__ import annotations

import argparse
import ctypes
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
ADAPTER_VERSION = "0.3.0"
SUPPORTED_LOGIC_BUNDLE_IDS = ("com.apple.mobilelogic", "com.apple.logic10")
SUPPORTED_TRANSPORT_CONTROL_ROLES = ("AXButton", "AXCheckBox")
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


PROCESS_SNAPSHOT_JXA = r'''
function run() {
  function safe(fn, fallback) { try { const value = fn(); return value === undefined ? fallback : value; } catch (_) { return fallback; } }
  const systemEvents = Application("/System/Library/CoreServices/System Events.app");
  const discovered = findLogicProcess(systemEvents, PREFERRED_BUNDLE_ID);
  if (discovered === null) {
    return JSON.stringify({ok: true, logic_running: false, bundle_identifier: null, process_identifier: null, frontmost: null});
  }
  return JSON.stringify({
    ok: true,
    logic_running: true,
    bundle_identifier: discovered.bundle_identifier,
    process_identifier: safe(() => Number(discovered.process.unixId()), null),
    frontmost: safe(() => Boolean(discovered.process.frontmost()), null)
  });
}
'''


WINDOW_DISCOVERY_JXA = r'''
function axValue(element, name) {
  try {
    const attrs = element.attributes.whose({name: name})();
    if (!attrs || attrs.length === 0) return null;
    const value = attrs[0].value();
    return value === undefined ? null : value;
  } catch (_) {
    return null;
  }
}

function asElementArray(value) {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) return value;
  try {
    if (typeof value.length === "number") {
      const result = [];
      for (let i = 0; i < value.length; i++) result.push(value[i]);
      return result;
    }
  } catch (_) {}
  return [value];
}

function completeProcessWindows(process) {
  let directWindows = null;
  try { directWindows = process.windows(); } catch (_) { directWindows = null; }
  if (directWindows !== null && directWindows.length > 0) {
    return {windows: directWindows, source: "process.windows"};
  }

  const attributeWindows = asElementArray(axValue(process, "AXWindows"));
  if (attributeWindows.length > 0) {
    return {windows: attributeWindows, source: "AXWindows"};
  }

  let directChildren = null;
  try { directChildren = process.uiElements(); } catch (_) { directChildren = null; }
  if (directChildren !== null) {
    const childWindows = directChildren.filter(element => {
      try { return String(element.role()) === "AXWindow"; } catch (_) { return false; }
    });
    if (childWindows.length > 0) {
      return {windows: childWindows, source: "process.uiElements.AXWindow"};
    }
  }

  return {windows: [], source: null, accessibility_authorized: directWindows !== null || directChildren !== null};
}

function frontmostProcess(systemEvents) {
  try {
    const processes = systemEvents.applicationProcesses.whose({frontmost: true})();
    if (processes && processes.length > 0) return processes[0];
  } catch (_) {}
  try {
    const processes = systemEvents.applicationProcesses();
    for (let i = 0; i < processes.length; i++) {
      try { if (Boolean(processes[i].frontmost())) return processes[i]; } catch (_) {}
    }
  } catch (_) {}
  return null;
}

function restoreWindowDiscoveryFocus(discovery) {
  if (!discovery.focus_temporarily_changed || discovery.previous_frontmost_process === null) return;
  try {
    discovery.previous_frontmost_process.frontmost = true;
    delay(0.05);
  } catch (_) {}
}

function discoverProcessWindows(systemEvents, process) {
  let frontmostBefore = null;
  try { frontmostBefore = Boolean(process.frontmost()); } catch (_) {}
  const passive = completeProcessWindows(process);
  if (passive.windows.length > 0) {
    return {
      windows: passive.windows,
      source: passive.source,
      complete: true,
      accessibility_authorized: true,
      diagnostic: null,
      frontmost_before: frontmostBefore,
      focus_temporarily_changed: false,
      previous_frontmost_process: null
    };
  }

  let mainWindow = axValue(process, "AXMainWindow");
  let focusedWindow = axValue(process, "AXFocusedWindow");
  const accessibilityAuthorized = passive.accessibility_authorized || mainWindow !== null || focusedWindow !== null;
  let diagnostic = accessibilityAuthorized ? "complete_window_set_not_exposed" : "accessibility_not_authorized";
  let previousFrontmost = null;
  let focusChanged = false;
  if (accessibilityAuthorized && frontmostBefore === false) {
    previousFrontmost = frontmostProcess(systemEvents);
    if (previousFrontmost === null) {
      diagnostic = "frontmost_retry_skipped_no_restore_target";
    } else {
      let sameProcess = false;
      try { sameProcess = Number(previousFrontmost.unixId()) === Number(process.unixId()); } catch (_) {}
      if (sameProcess) {
        previousFrontmost = null;
        diagnostic = "frontmost_retry_skipped_no_distinct_restore_target";
      } else {
        try {
          process.frontmost = true;
          delay(0.2);
          focusChanged = true;
        } catch (_) {
          diagnostic = "frontmost_retry_activation_failed";
        }
      }
      if (focusChanged) {
        const foreground = completeProcessWindows(process);
        if (foreground.windows.length > 0) {
          return {
            windows: foreground.windows,
            source: "frontmost-retry." + foreground.source,
            complete: true,
            accessibility_authorized: true,
            diagnostic: null,
            frontmost_before: frontmostBefore,
            focus_temporarily_changed: true,
            previous_frontmost_process: previousFrontmost
          };
        }
        diagnostic = "frontmost_retry_no_complete_window_set";
        mainWindow = axValue(process, "AXMainWindow");
        focusedWindow = axValue(process, "AXFocusedWindow");
      }
    }
  }

  if (mainWindow !== null) {
    return {
      windows: [mainWindow], source: "AXMainWindow", complete: false, accessibility_authorized: true,
      diagnostic: diagnostic, frontmost_before: frontmostBefore, focus_temporarily_changed: focusChanged,
      previous_frontmost_process: previousFrontmost
    };
  }
  if (focusedWindow !== null) {
    return {
      windows: [focusedWindow], source: "AXFocusedWindow", complete: false, accessibility_authorized: true,
      diagnostic: diagnostic, frontmost_before: frontmostBefore, focus_temporarily_changed: focusChanged,
      previous_frontmost_process: previousFrontmost
    };
  }

  return {
    windows: [],
    source: passive.accessibility_authorized ? "process.windows" : null,
    complete: passive.accessibility_authorized,
    accessibility_authorized: accessibilityAuthorized,
    diagnostic: diagnostic,
    frontmost_before: frontmostBefore,
    focus_temporarily_changed: focusChanged,
    previous_frontmost_process: previousFrontmost
  };
}

function boundedDescendants(root, maxElements, maxDepth) {
  const first = axValue(root, "AXChildren");
  if (first === null) return {elements: [], observed: false, truncated: false};
  const queue = asElementArray(first).map(element => ({element: element, depth: 1}));
  const elements = [];
  let truncated = false;
  while (queue.length > 0) {
    const item = queue.shift();
    if (elements.length >= maxElements) { truncated = true; break; }
    elements.push(item.element);
    const children = asElementArray(axValue(item.element, "AXChildren"));
    if (children.length === 0) continue;
    if (item.depth >= maxDepth) { truncated = true; continue; }
    for (let i = 0; i < children.length; i++) queue.push({element: children[i], depth: item.depth + 1});
  }
  return {elements: elements, observed: true, truncated: truncated || queue.length > 0};
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
    return JSON.stringify({ok: true, logic_running: false, bundle_identifier: null, accessibility_authorized: false, window_discovery_source: null, window_discovery_diagnostic: "logic_process_not_found", window_set_complete: false, focus_temporarily_changed: false, transport_controls_observed: false, transport_controls_complete: false, windows: [], controls: []});
  }
  const process = discovered.process;
  const windowDiscovery = discoverProcessWindows(systemEvents, process);
  try {
    const windows = windowDiscovery.windows;
    if (!windowDiscovery.accessibility_authorized) {
      return JSON.stringify({ok: true, logic_running: true, bundle_identifier: discovered.bundle_identifier, accessibility_authorized: false, window_discovery_source: null, window_discovery_diagnostic: windowDiscovery.diagnostic, window_set_complete: false, focus_temporarily_changed: windowDiscovery.focus_temporarily_changed, transport_controls_observed: false, transport_controls_complete: false, windows: [], controls: []});
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
      const isModal = safe(() => Boolean(attr(windows[i], "AXModal")), false);
      if (subrole === "AXDialog" || isModal || (sheets && sheets.length > 0)) modal = true;
      windowRows.push({
        title: text(safe(() => windows[i].name(), null)),
        role: text(safe(() => windows[i].role(), null)),
        subrole: subrole,
        document: text(attr(windows[i], "AXDocument")),
        main: Boolean(attr(windows[i], "AXMain")),
        modal: isModal,
        sheet_count: sheets ? sheets.length : 0
      });
    }
    const controls = [];
    let controlsObserved = false;
    let controlsTruncated = false;
    if (mainWindow !== null && INCLUDE_CONTROLS) {
      const traversal = boundedDescendants(mainWindow, 4000, 32);
      controlsObserved = traversal.observed;
      controlsTruncated = traversal.truncated;
      for (let i = 0; i < traversal.elements.length; i++) {
        const element = traversal.elements[i];
        const role = text(safe(() => element.role(), null));
        if (!SUPPORTED_TRANSPORT_CONTROL_ROLES.includes(role) && !["AXRadioButton", "AXTextField", "AXStaticText"].includes(role)) continue;
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
      frontmost: windowDiscovery.frontmost_before,
      modal_dialog: modal ? true : (windowDiscovery.complete ? false : null),
      window_discovery_source: windowDiscovery.source,
      window_discovery_diagnostic: windowDiscovery.diagnostic,
      window_set_complete: windowDiscovery.complete,
      focus_temporarily_changed: windowDiscovery.focus_temporarily_changed,
      transport_controls_observed: controlsObserved,
      transport_controls_complete: controlsObserved && !controlsTruncated,
      control_tree_source: controlsObserved ? "bounded-AXChildren" : null,
      windows: windowRows,
      controls: controls,
      controls_truncated: controlsTruncated
    });
  } finally {
    restoreWindowDiscoveryFocus(windowDiscovery);
  }
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
  const windowDiscovery = discoverProcessWindows(systemEvents, process);
  try {
    const windows = windowDiscovery.windows;
    if (!windowDiscovery.accessibility_authorized) return JSON.stringify({ok: false, definitive: true, reason: "accessibility_not_authorized"});
    let mainWindow = null;
    for (let i = 0; i < windows.length; i++) {
      const sheets = safe(() => windows[i].sheets(), []);
      const subrole = text(safe(() => windows[i].subrole(), null));
      const isModal = safe(() => Boolean(attr(windows[i], "AXModal")), false);
      if (subrole === "AXDialog" || isModal || (sheets && sheets.length > 0)) return JSON.stringify({ok: false, definitive: true, reason: "modal_dialog_present"});
      if (mainWindow === null && Boolean(attr(windows[i], "AXMain"))) mainWindow = windows[i];
    }
    if (!windowDiscovery.complete) return JSON.stringify({ok: false, definitive: true, reason: "window_set_incomplete", diagnostic: windowDiscovery.diagnostic});
    if (mainWindow === null && windows.length > 0) mainWindow = windows[0];
    if (mainWindow === null) return JSON.stringify({ok: false, definitive: true, reason: "project_window_not_found"});
    const expectedName = basename(EXPECTED_PROJECT);
    const documentValue = text(attr(mainWindow, "AXDocument"));
    const windowTitle = text(safe(() => mainWindow.name(), null));
    if (documentValue) {
      if (basename(documentValue) !== expectedName) return JSON.stringify({ok: false, definitive: true, reason: "project_mismatch"});
    } else if (windowTitle === null) {
      return JSON.stringify({ok: false, definitive: true, reason: "project_identity_unavailable", diagnostic: "main_window_has_no_document_or_title"});
    } else if (windowTitle !== expectedName) {
      return JSON.stringify({ok: false, definitive: true, reason: "project_mismatch"});
    }
    const traversal = boundedDescendants(mainWindow, 4000, 32);
    if (!traversal.observed) return JSON.stringify({ok: false, definitive: true, reason: "transport_control_tree_unavailable"});
    if (traversal.truncated) return JSON.stringify({ok: false, definitive: true, reason: "transport_control_tree_truncated"});
    const transportControls = traversal.elements.filter(element => SUPPORTED_TRANSPORT_CONTROL_ROLES.includes(text(safe(() => element.role(), null))));
    const playLabels = ["play", "再生"];
    const stopLabels = ["stop", "停止"];
    const beginningLabels = ["go to beginning", "先頭へ移動", "先頭に移動"];
    const playControls = transportControls.filter(element => matches(element, playLabels));
    const stopControls = transportControls.filter(element => matches(element, stopLabels));
    const beginningControls = transportControls.filter(element => matches(element, beginningLabels));
    const play = playControls.length > 0 ? playControls[0] : null;
    const stop = stopControls.length > 0 ? stopControls[0] : null;
    const beginning = beginningControls.length > 0 ? beginningControls[0] : null;
    let playing = play ? truth(safe(() => play.value(), null)) : null;
    if (playing === null && stop && !beginning) playing = true;
    if (playing === null && beginning && !stop) playing = false;
    if (playing === null) return JSON.stringify({ok: false, definitive: true, reason: "transport_state_not_observable"});
    const wantsPlaying = REQUESTED_OPERATION === "transport.play";
    if (playing === wantsPlaying) return JSON.stringify({ok: true, performed: false, already_satisfied: true, window_discovery_source: windowDiscovery.source, before: {is_playing: playing}});
    const targetCandidates = wantsPlaying ? playControls : stopControls;
    if (targetCandidates.length === 0) return JSON.stringify({ok: false, definitive: true, reason: wantsPlaying ? "play_control_not_found" : "stop_control_not_found"});
    const target = targetCandidates.find(element => {
      const candidateActions = safe(() => element.actions(), []);
      return candidateActions.some(action => text(safe(() => action.name(), null)) === "AXPress");
    });
    if (!target) return JSON.stringify({ok: false, definitive: true, reason: "ax_press_not_supported"});
    const actions = safe(() => target.actions(), []);
    const press = actions.find(action => text(safe(() => action.name(), null)) === "AXPress");
    if (!press) return JSON.stringify({ok: false, definitive: true, reason: "ax_press_not_supported"});
    press.perform();
    return JSON.stringify({ok: true, performed: true, already_satisfied: false, bundle_identifier: discovered.bundle_identifier, window_discovery_source: windowDiscovery.source, before: {is_playing: playing}});
  } finally {
    restoreWindowDiscoveryFocus(windowDiscovery);
  }
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
        "SUPPORTED_TRANSPORT_CONTROL_ROLES": list(SUPPORTED_TRANSPORT_CONTROL_ROLES),
        "INCLUDE_CONTROLS": True,
        **(constants or {}),
    }
    prefix = "".join(f"const {name} = {json.dumps(value)};\n" for name, value in assignments.items())
    return prefix + BUNDLE_DISCOVERY_JXA + WINDOW_DISCOVERY_JXA + source


def render_process_jxa(preferred_bundle_id: str | None = None) -> str:
    assignments = {
        "SUPPORTED_LOGIC_BUNDLE_IDS": list(SUPPORTED_LOGIC_BUNDLE_IDS),
        "PREFERRED_BUNDLE_ID": preferred_bundle_id,
    }
    prefix = "".join(f"const {name} = {json.dumps(value)};\n" for name, value in assignments.items())
    return prefix + BUNDLE_DISCOVERY_JXA + PROCESS_SNAPSHOT_JXA


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
    transport_controls = [
        control
        for control in controls
        if isinstance(control, dict) and control.get("role") in SUPPORTED_TRANSPORT_CONTROL_ROLES
    ]
    play_controls = [control for control in transport_controls if matches_label(control, PLAY_LABELS)]
    stop_controls = [control for control in transport_controls if matches_label(control, STOP_LABELS)]
    beginning_controls = [control for control in transport_controls if matches_label(control, BEGINNING_LABELS)]
    play = next(iter(play_controls), None)
    stop = next(iter(stop_controls), None)
    beginning = next(iter(beginning_controls), None)
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
        "play_control_available": any("AXPress" in control.get("actions", []) for control in play_controls),
        "stop_control_available": any("AXPress" in control.get("actions", []) for control in stop_controls),
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
        return {
            "current_project": None,
            "current_project_unavailable": True,
            "current_project_unavailable_reason": "project_window_not_found",
            "window_project_name": None,
            "project_identity_source": None,
        }
    current = normalize_document_path(window.get("document"))
    title = window.get("title") if isinstance(window.get("title"), str) else None
    return {
        "current_project": current,
        "current_project_unavailable": current is None,
        "current_project_unavailable_reason": (
            None if current is not None else "document_path_not_exposed" if title else "main_window_has_no_document_or_title"
        ),
        "window_project_name": title,
        "project_identity_source": (
            window.get("document_source", "AXDocument")
            if current is not None
            else window.get("title_source", "AXTitle") if title else None
        ),
    }


def runtime_capabilities(snapshot: dict[str, Any]) -> list[str]:
    capabilities = ["app.status"]
    if snapshot.get("logic_running") is not True or snapshot.get("accessibility_authorized") is not True:
        return capabilities
    if main_window(snapshot) is not None:
        capabilities.append("project.current")
    transport = transport_from_controls(snapshot.get("controls"))
    if (
        snapshot.get("transport_controls_observed") is True
        and snapshot.get("transport_controls_complete") is True
        and transport["is_playing"] is not None
    ):
        capabilities.append("transport.state")
        if snapshot.get("window_set_complete") is True:
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
    if project["window_project_name"] is None:
        reason = project["current_project_unavailable_reason"]
        raise AdapterFailure(
            "project_identity_unavailable",
            f"Logic main window does not expose a project document or title: {reason}",
            definitive=True,
        )
    if project["window_project_name"] != Path(expected).name:
        raise AdapterFailure("project_mismatch", "Logic window title does not exactly match expected .logicx name", definitive=True)


AX_ERROR_NAMES = {
    0: "success",
    -25200: "failure",
    -25201: "illegal_argument",
    -25202: "invalid_ui_element",
    -25203: "invalid_observer",
    -25204: "cannot_complete",
    -25205: "attribute_unsupported",
    -25206: "action_unsupported",
    -25207: "notification_unsupported",
    -25208: "not_implemented",
    -25209: "notification_already_registered",
    -25210: "notification_not_registered",
    -25211: "api_disabled",
    -25212: "no_value",
    -25213: "parameterized_attribute_unsupported",
    -25214: "not_enough_precision",
}
AX_LEAF_ERRORS = {-25205, -25212}


def ax_error_name(code: int) -> str:
    return AX_ERROR_NAMES.get(code, f"ax_error_{code}")


class NativeAXBridge:
    """Bounded, dependency-free AXUIElement bridge for one application PID."""

    UTF8_ENCODING = 0x08000100
    MAX_WINDOWS = 64
    MAX_CONTROLS = 4000
    MAX_DEPTH = 32

    def __init__(self, process_identifier: int, timeout_seconds: float):
        if not isinstance(process_identifier, int) or process_identifier <= 0:
            raise AdapterFailure("invalid_process_identifier", "Logic process identifier is invalid", definitive=True)
        self.process_identifier = process_identifier
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 2.0))
        self._application_services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        self._core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._keys: dict[str, int] = {}
        self._owned_elements: list[int] = []
        self._configure_functions()
        system_wide = self._application_services.AXUIElementCreateSystemWide()
        self.system_wide = int(system_wide) if system_wide else None
        if self.system_wide:
            self._owned_elements.append(self.system_wide)
        application = self._application_services.AXUIElementCreateApplication(process_identifier)
        if not application:
            while self._owned_elements:
                self._core_foundation.CFRelease(ctypes.c_void_p(self._owned_elements.pop()))
            raise AdapterFailure("native_ax_unavailable", "AXUIElementCreateApplication returned null", definitive=True)
        self.application = int(application)
        self._owned_elements.append(self.application)
        global_timeout_code = (
            int(
                self._application_services.AXUIElementSetMessagingTimeout(
                    ctypes.c_void_p(self.system_wide), ctypes.c_float(self.timeout_seconds)
                )
            )
            if self.system_wide
            else -25200
        )
        application_timeout_code = int(self._application_services.AXUIElementSetMessagingTimeout(
            ctypes.c_void_p(self.application), ctypes.c_float(self.timeout_seconds)
        ))
        self.timeout_configured = global_timeout_code == 0 and application_timeout_code == 0

    def _configure_functions(self) -> None:
        ax = self._application_services
        cf = self._core_foundation
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        ax.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
        ax.AXUIElementCreateApplication.restype = ctypes.c_void_p
        ax.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        ax.AXUIElementSetMessagingTimeout.argtypes = [ctypes.c_void_p, ctypes.c_float]
        ax.AXUIElementSetMessagingTimeout.restype = ctypes.c_int32
        ax.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ax.AXUIElementCopyAttributeValue.restype = ctypes.c_int32
        ax.AXUIElementGetAttributeValueCount.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_long),
        ]
        ax.AXUIElementGetAttributeValueCount.restype = ctypes.c_int32
        ax.AXUIElementCopyAttributeValues.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ax.AXUIElementCopyAttributeValues.restype = ctypes.c_int32
        ax.AXUIElementCopyActionNames.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        ax.AXUIElementCopyActionNames.restype = ctypes.c_int32
        ax.AXUIElementPerformAction.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ax.AXUIElementPerformAction.restype = ctypes.c_int32
        ax.AXUIElementGetTypeID.restype = ctypes.c_ulong

        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringGetLength.argtypes = [ctypes.c_void_p]
        cf.CFStringGetLength.restype = ctypes.c_long
        cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFNumberGetTypeID.restype = ctypes.c_ulong
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFArrayGetTypeID.restype = ctypes.c_ulong
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFURLGetTypeID.restype = ctypes.c_ulong
        cf.CFURLGetString.argtypes = [ctypes.c_void_p]
        cf.CFURLGetString.restype = ctypes.c_void_p
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFEqual.restype = ctypes.c_bool
        cf.CFHash.argtypes = [ctypes.c_void_p]
        cf.CFHash.restype = ctypes.c_ulong
        cf.CFRetain.argtypes = [ctypes.c_void_p]
        cf.CFRetain.restype = ctypes.c_void_p
        cf.CFRelease.argtypes = [ctypes.c_void_p]

    def __enter__(self) -> NativeAXBridge:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        cf = self._core_foundation
        if self.system_wide:
            self._application_services.AXUIElementSetMessagingTimeout(
                ctypes.c_void_p(self.system_wide), ctypes.c_float(0.0)
            )
        while self._owned_elements:
            cf.CFRelease(ctypes.c_void_p(self._owned_elements.pop()))
        for value in self._keys.values():
            cf.CFRelease(ctypes.c_void_p(value))
        self._keys.clear()

    def trusted(self) -> bool:
        return bool(self._application_services.AXIsProcessTrusted()) and self.timeout_configured

    def _key(self, name: str) -> int:
        if name not in self._keys:
            value = self._core_foundation.CFStringCreateWithCString(
                None, name.encode("utf-8"), self.UTF8_ENCODING
            )
            if not value:
                raise AdapterFailure("native_ax_unavailable", f"cannot create AX key: {name}", definitive=True)
            self._keys[name] = int(value)
        return self._keys[name]

    def _release(self, value: int | None) -> None:
        if value:
            self._core_foundation.CFRelease(ctypes.c_void_p(value))

    def _retain_element(self, value: int) -> int:
        self._core_foundation.CFRetain(ctypes.c_void_p(value))
        self._owned_elements.append(value)
        return value

    def _copy_raw(self, element: int, attribute: str) -> tuple[int, int | None]:
        output = ctypes.c_void_p()
        code = int(
            self._application_services.AXUIElementCopyAttributeValue(
                ctypes.c_void_p(element), ctypes.c_void_p(self._key(attribute)), ctypes.byref(output)
            )
        )
        return code, int(output.value) if output.value else None

    def _cf_string(self, value: int) -> str | None:
        cf = self._core_foundation
        length = cf.CFStringGetLength(ctypes.c_void_p(value))
        size = cf.CFStringGetMaximumSizeForEncoding(length, self.UTF8_ENCODING) + 1
        buffer = ctypes.create_string_buffer(max(size, 1))
        if not cf.CFStringGetCString(
            ctypes.c_void_p(value), buffer, len(buffer), self.UTF8_ENCODING
        ):
            return None
        return buffer.value.decode("utf-8", errors="replace")

    def _python_value(self, value: int) -> Any:
        cf = self._core_foundation
        type_id = cf.CFGetTypeID(ctypes.c_void_p(value))
        if type_id == cf.CFStringGetTypeID():
            return self._cf_string(value)
        if type_id == cf.CFBooleanGetTypeID():
            return bool(cf.CFBooleanGetValue(ctypes.c_void_p(value)))
        if type_id == cf.CFNumberGetTypeID():
            number = ctypes.c_double()
            if cf.CFNumberGetValue(ctypes.c_void_p(value), 13, ctypes.byref(number)):
                return int(number.value) if number.value.is_integer() else number.value
            return None
        if type_id == cf.CFURLGetTypeID():
            string_value = cf.CFURLGetString(ctypes.c_void_p(value))
            return self._cf_string(int(string_value)) if string_value else None
        return None

    def _scalar(self, element: int, attribute: str) -> tuple[int, Any]:
        code, value = self._copy_raw(element, attribute)
        if code != 0 or value is None:
            return code, None
        try:
            return code, self._python_value(value)
        finally:
            self._release(value)

    def _element(self, element: int, attribute: str) -> tuple[int, int | None]:
        code, value = self._copy_raw(element, attribute)
        if code != 0 or value is None:
            return code, None
        if self._core_foundation.CFGetTypeID(ctypes.c_void_p(value)) != self._application_services.AXUIElementGetTypeID():
            self._release(value)
            return -25201, None
        self._owned_elements.append(value)
        return code, value

    def _element_array(
        self, element: int, attribute: str, limit: int
    ) -> tuple[int, list[int], bool]:
        count = ctypes.c_long()
        code = int(
            self._application_services.AXUIElementGetAttributeValueCount(
                ctypes.c_void_p(element), ctypes.c_void_p(self._key(attribute)), ctypes.byref(count)
            )
        )
        if code != 0:
            return code, [], False
        if count.value <= 0:
            return 0, [], False
        requested = min(count.value, limit + 1)
        output = ctypes.c_void_p()
        code = int(
            self._application_services.AXUIElementCopyAttributeValues(
                ctypes.c_void_p(element),
                ctypes.c_void_p(self._key(attribute)),
                0,
                requested,
                ctypes.byref(output),
            )
        )
        if code != 0 or not output.value:
            return code, [], False
        array = int(output.value)
        elements: list[int] = []
        try:
            array_count = self._core_foundation.CFArrayGetCount(ctypes.c_void_p(array))
            for index in range(min(array_count, limit)):
                value = self._core_foundation.CFArrayGetValueAtIndex(ctypes.c_void_p(array), index)
                if not value:
                    continue
                pointer = int(value)
                if (
                    self._core_foundation.CFGetTypeID(ctypes.c_void_p(pointer))
                    == self._application_services.AXUIElementGetTypeID()
                ):
                    elements.append(self._retain_element(pointer))
            return 0, elements, count.value > limit or array_count > limit
        finally:
            self._release(array)

    def _action_names(self, element: int) -> tuple[int, list[str]]:
        output = ctypes.c_void_p()
        code = int(
            self._application_services.AXUIElementCopyActionNames(
                ctypes.c_void_p(element), ctypes.byref(output)
            )
        )
        if code != 0 or not output.value:
            return code, []
        array = int(output.value)
        try:
            names = []
            count = self._core_foundation.CFArrayGetCount(ctypes.c_void_p(array))
            for index in range(count):
                value = self._core_foundation.CFArrayGetValueAtIndex(ctypes.c_void_p(array), index)
                if value:
                    converted = self._python_value(int(value))
                    if isinstance(converted, str):
                        names.append(converted)
            return 0, names
        finally:
            self._release(array)

    def _same_element(self, left: int | None, right: int | None) -> bool:
        return bool(
            left
            and right
            and self._core_foundation.CFEqual(ctypes.c_void_p(left), ctypes.c_void_p(right))
        )

    def _window_row(self, window: int, main_window: int | None) -> tuple[dict[str, Any], bool]:
        _, title = self._scalar(window, "AXTitle")
        title_source = "AXTitle" if isinstance(title, str) and title else None
        if not title_source:
            _, title_element = self._element(window, "AXTitleUIElement")
            if title_element is not None:
                _, title = self._scalar(title_element, "AXValue")
                if not isinstance(title, str) or not title:
                    _, title = self._scalar(title_element, "AXTitle")
                if isinstance(title, str) and title:
                    title_source = "AXTitleUIElement"
        _, role = self._scalar(window, "AXRole")
        _, subrole = self._scalar(window, "AXSubrole")
        _, document = self._scalar(window, "AXDocument")
        modal_code, modal_value = self._scalar(window, "AXModal")
        sheet_code, sheets, sheet_truncated = self._element_array(window, "AXSheets", self.MAX_WINDOWS)
        modal_unknown = modal_code not in ({0} | AX_LEAF_ERRORS) or sheet_code not in ({0} | AX_LEAF_ERRORS)
        main_code, main_value = self._scalar(window, "AXMain")
        is_main = self._same_element(window, main_window) or (main_code == 0 and main_value is True)
        return (
            {
                "title": title if isinstance(title, str) else None,
                "title_source": title_source,
                "role": role if isinstance(role, str) else None,
                "subrole": subrole if isinstance(subrole, str) else None,
                "document": document if isinstance(document, str) else None,
                "document_source": "AXDocument" if isinstance(document, str) and document else None,
                "main": is_main,
                "modal": modal_value is True,
                "sheet_count": len(sheets),
                "sheets_truncated": sheet_truncated,
            },
            modal_unknown or sheet_truncated,
        )

    def _controls(self, main_window: int) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], int]], bool, bool, str | None]:
        code, initial, initial_truncated = self._element_array(
            main_window, "AXChildren", self.MAX_CONTROLS
        )
        if code not in ({0} | AX_LEAF_ERRORS):
            return [], [], False, False, f"native_axchildren_{ax_error_name(code)}"
        if code in AX_LEAF_ERRORS:
            return [], [], False, False, "native_axchildren_unavailable"
        queue = [(element, 1) for element in initial]
        visited: list[int] = []
        visited_hashes: dict[int, list[int]] = {}
        controls: list[dict[str, Any]] = []
        control_elements: list[tuple[dict[str, Any], int]] = []
        truncated = initial_truncated
        diagnostic = None
        index = 0
        while index < len(queue):
            element, depth = queue[index]
            index += 1
            element_hash = int(self._core_foundation.CFHash(ctypes.c_void_p(element)))
            bucket = visited_hashes.setdefault(element_hash, [])
            if any(self._same_element(element, previous) for previous in bucket):
                continue
            bucket.append(element)
            if len(visited) >= self.MAX_CONTROLS:
                truncated = True
                break
            visited.append(element)
            _, role = self._scalar(element, "AXRole")
            if role in SUPPORTED_TRANSPORT_CONTROL_ROLES or role in {
                "AXRadioButton",
                "AXTextField",
                "AXStaticText",
            }:
                _, title = self._scalar(element, "AXTitle")
                _, description = self._scalar(element, "AXDescription")
                _, help_text = self._scalar(element, "AXHelp")
                _, identifier = self._scalar(element, "AXIdentifier")
                _, value = self._scalar(element, "AXValue")
                _, enabled = self._scalar(element, "AXEnabled")
                _, actions = self._action_names(element)
                row = {
                    "role": role,
                    "title": title,
                    "description": description,
                    "help": help_text,
                    "identifier": identifier,
                    "value": value,
                    "enabled": enabled,
                    "actions": actions,
                }
                controls.append(row)
                control_elements.append((row, element))
            remaining = self.MAX_CONTROLS - len(visited)
            child_code, children, child_truncated = self._element_array(
                element, "AXChildren", max(remaining, 0)
            )
            if child_code in AX_LEAF_ERRORS:
                continue
            if child_code != 0:
                truncated = True
                diagnostic = f"native_axchildren_{ax_error_name(child_code)}"
                continue
            if children and depth >= self.MAX_DEPTH:
                truncated = True
                diagnostic = "native_axchildren_depth_limit"
                continue
            if child_truncated:
                truncated = True
                diagnostic = "native_axchildren_element_limit"
            queue.extend((child, depth + 1) for child in children)
        if truncated and diagnostic is None:
            diagnostic = "native_axchildren_element_limit"
        return controls, control_elements, True, not truncated, diagnostic

    def snapshot(self, *, include_controls: bool, include_elements: bool = False) -> dict[str, Any]:
        if not self.trusted():
            return {
                "accessibility_authorized": False,
                "window_discovery_source": "AXUIElement",
                "window_discovery_diagnostic": (
                    "native_ax_client_not_trusted"
                    if not self._application_services.AXIsProcessTrusted()
                    else "native_ax_timeout_configuration_failed"
                ),
                "window_set_complete": False,
                "transport_controls_observed": False,
                "transport_controls_complete": False,
                "windows": [],
                "controls": [],
            }
        windows_code, windows, windows_truncated = self._element_array(
            self.application, "AXWindows", self.MAX_WINDOWS
        )
        _, main_window = self._element(self.application, "AXMainWindow")
        _, focused_window = self._element(self.application, "AXFocusedWindow")
        complete = windows_code == 0 and not windows_truncated
        diagnostic = None
        if windows_code != 0:
            diagnostic = f"native_axwindows_{ax_error_name(windows_code)}"
        elif windows_truncated:
            diagnostic = "native_axwindows_limit"
        if main_window is not None and not any(self._same_element(main_window, window) for window in windows):
            complete = False
            diagnostic = "native_axwindows_omits_main_window"
        selected_windows = windows
        source = "AXUIElement.AXWindows"
        if not selected_windows and main_window is not None:
            selected_windows = [main_window]
            source = "AXUIElement.AXMainWindow"
            complete = False
            diagnostic = diagnostic or "native_axwindows_omits_main_window"
        elif not selected_windows and focused_window is not None:
            selected_windows = [focused_window]
            source = "AXUIElement.AXFocusedWindow"
            complete = False
            diagnostic = diagnostic or "native_axwindows_omits_focused_window"
        window_rows = []
        modal = False
        modal_unknown = False
        for window in selected_windows:
            row, unknown = self._window_row(window, main_window)
            window_rows.append(row)
            modal = modal or row["subrole"] == "AXDialog" or row["modal"] or row["sheet_count"] > 0
            modal_unknown = modal_unknown or unknown
        application_document_code, application_document = self._scalar(self.application, "AXDocument")
        if application_document_code == 0 and isinstance(application_document, str):
            for row in window_rows:
                if row["main"] and not row["document"]:
                    row["document"] = application_document
                    row["document_source"] = "application.AXDocument"
        selected_main = next(
            (
                window
                for window, row in zip(selected_windows, window_rows, strict=True)
                if row["main"] is True
            ),
            None,
        )
        if selected_windows and selected_main is None:
            complete = False
            diagnostic = diagnostic or "native_ax_main_window_unavailable"
        controls: list[dict[str, Any]] = []
        control_elements: list[tuple[dict[str, Any], int]] = []
        controls_observed = False
        controls_complete = False
        control_diagnostic = None
        if include_controls and selected_main is not None:
            controls, control_elements, controls_observed, controls_complete, control_diagnostic = self._controls(
                selected_main
            )
        result = {
            "accessibility_authorized": True,
            "modal_dialog": True if modal else False if complete and not modal_unknown else None,
            "window_discovery_source": source,
            "window_discovery_diagnostic": diagnostic,
            "window_set_complete": complete,
            "focus_temporarily_changed": False,
            "transport_controls_observed": controls_observed,
            "transport_controls_complete": controls_complete,
            "control_tree_source": "AXUIElement.AXChildren" if controls_observed else None,
            "control_tree_diagnostic": control_diagnostic,
            "windows": window_rows,
            "controls": controls,
            "controls_truncated": controls_observed and not controls_complete,
        }
        if include_elements:
            result["_control_elements"] = control_elements
        return result

    def dispatch_transport(self, operation: str, expected_project: str) -> dict[str, Any]:
        snapshot = self.snapshot(include_controls=True, include_elements=True)
        if snapshot.get("accessibility_authorized") is not True:
            raise AdapterFailure("accessibility_not_authorized", "native AX client is not trusted", definitive=True)
        if snapshot.get("window_set_complete") is not True:
            raise AdapterFailure(
                "window_set_incomplete",
                str(snapshot.get("window_discovery_diagnostic") or "native AX window set is incomplete"),
                definitive=True,
            )
        if snapshot.get("modal_dialog") is not False:
            raise AdapterFailure("modal_dialog_present", "Logic modal state is unsafe or unknown", definitive=True)
        assert_project_binding(snapshot, expected_project)
        if snapshot.get("transport_controls_observed") is not True:
            raise AdapterFailure("transport_control_tree_unavailable", "native AX control tree is unavailable", definitive=True)
        if snapshot.get("transport_controls_complete") is not True:
            raise AdapterFailure("transport_control_tree_truncated", "native AX control tree is incomplete", definitive=True)
        transport = transport_from_controls(snapshot.get("controls"))
        if transport["is_playing"] is None:
            raise AdapterFailure("transport_state_not_observable", "native AX transport state is unavailable", definitive=True)
        wants_playing = operation == "transport.play"
        if transport["is_playing"] is wants_playing:
            return {
                "ok": True,
                "performed": False,
                "already_satisfied": True,
                "window_discovery_source": snapshot.get("window_discovery_source"),
                "before": {"is_playing": transport["is_playing"]},
            }
        labels = PLAY_LABELS if wants_playing else STOP_LABELS
        candidates = [
            (row, element)
            for row, element in snapshot.get("_control_elements", [])
            if row.get("role") in SUPPORTED_TRANSPORT_CONTROL_ROLES
            and matches_label(row, labels)
            and "AXPress" in row.get("actions", [])
        ]
        if not candidates:
            raise AdapterFailure(
                "play_control_not_found" if wants_playing else "stop_control_not_found",
                "matching native AX transport control with AXPress was not found",
                definitive=True,
            )
        code = int(
            self._application_services.AXUIElementPerformAction(
                ctypes.c_void_p(candidates[0][1]), ctypes.c_void_p(self._key("AXPress"))
            )
        )
        if code != 0:
            definitive = code in {-25202, -25205, -25206, -25208, -25211, -25212}
            raise AdapterFailure(
                "ax_press_failed",
                f"AXUIElementPerformAction failed: {ax_error_name(code)}",
                definitive=definitive,
                may_have_dispatched=not definitive,
            )
        return {
            "ok": True,
            "performed": True,
            "already_satisfied": False,
            "window_discovery_source": snapshot.get("window_discovery_source"),
            "before": {"is_playing": transport["is_playing"]},
        }


class MacOSAccessibilityBackend:
    def __init__(self, timeout_seconds: float = 10.0, native_bridge_factory: Any = NativeAXBridge):
        self.timeout_seconds = timeout_seconds
        self.native_bridge_factory = native_bridge_factory

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

    def _process_snapshot(self, preferred_bundle_id: str | None = None) -> dict[str, Any]:
        return self._run_jxa(render_process_jxa(preferred_bundle_id))

    @staticmethod
    def _snapshot_score(snapshot: dict[str, Any], include_controls: bool) -> int:
        score = 0
        if snapshot.get("accessibility_authorized") is True:
            score += 1
        if main_window(snapshot) is not None:
            score += 2
        if snapshot.get("window_set_complete") is True:
            score += 8
        project = project_from_snapshot(snapshot)
        if project["current_project"] is not None or project["window_project_name"] is not None:
            score += 4
        if include_controls and snapshot.get("transport_controls_complete") is True:
            score += 8
        return score

    def snapshot(self, *, include_controls: bool = True) -> dict[str, Any]:
        process = self._process_snapshot()
        unlocked = self._screen_unlocked()
        if process.get("logic_running") is not True:
            return {
                "ok": True,
                "logic_running": False,
                "bundle_identifier": None,
                "process_identifier": None,
                "screen_unlocked": unlocked,
                "accessibility_authorized": False,
                "accessibility_backend": None,
                "frontmost": None,
                "modal_dialog": None,
                "window_discovery_source": None,
                "window_discovery_diagnostic": "logic_process_not_found",
                "window_set_complete": False,
                "focus_temporarily_changed": False,
                "transport_controls_observed": False,
                "transport_controls_complete": False,
                "windows": [],
                "controls": [],
            }
        process_identifier = process.get("process_identifier")
        native: dict[str, Any] | None = None
        native_error: str | None = None
        if isinstance(process_identifier, int) and process_identifier > 0:
            try:
                with self.native_bridge_factory(process_identifier, self.timeout_seconds) as bridge:
                    native = bridge.snapshot(include_controls=include_controls)
            except (AdapterFailure, OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError) as exc:
                native_error = f"native_ax_bridge_error:{type(exc).__name__}"
        legacy: dict[str, Any] | None = None
        use_native_without_fallback = native is not None and native.get("window_set_complete") is True
        if include_controls and native is not None and native.get("transport_controls_complete") is not True:
            use_native_without_fallback = False
        if not use_native_without_fallback:
            legacy = self._run_jxa(render_jxa(SNAPSHOT_JXA, {"INCLUDE_CONTROLS": include_controls}))
        candidates = [candidate for candidate in (native, legacy) if isinstance(candidate, dict)]
        if not candidates:
            raise AdapterFailure("adapter_unavailable", "no Accessibility snapshot backend is available", definitive=True)
        snapshot = max(candidates, key=lambda candidate: self._snapshot_score(candidate, include_controls))
        snapshot["ok"] = True
        snapshot["logic_running"] = True
        snapshot["bundle_identifier"] = process.get("bundle_identifier")
        snapshot["process_identifier"] = process_identifier
        snapshot["screen_unlocked"] = unlocked
        snapshot["frontmost"] = process.get("frontmost")
        snapshot["accessibility_backend"] = (
            "AXUIElement" if snapshot is native else "SystemEvents"
        )
        snapshot["native_accessibility_diagnostic"] = (
            native_error
            or (native.get("window_discovery_diagnostic") if isinstance(native, dict) else "native_ax_unavailable")
        )
        return snapshot

    def dispatch_transport(
        self,
        operation: str,
        expected_project: str,
        bundle_identifier: str,
        process_identifier: int | None = None,
    ) -> dict[str, Any]:
        if bundle_identifier not in SUPPORTED_LOGIC_BUNDLE_IDS:
            raise AdapterFailure("unsupported_logic_bundle", "observed Logic bundle identifier is unsupported", definitive=True)
        process = self._process_snapshot(bundle_identifier)
        current_process_identifier = process.get("process_identifier")
        if process.get("logic_running") is not True or not isinstance(current_process_identifier, int):
            raise AdapterFailure("logic_not_running", "Logic Pro process disappeared before dispatch", definitive=True)
        if process_identifier is not None and current_process_identifier != process_identifier:
            raise AdapterFailure("logic_process_changed", "Logic Pro process changed after preflight snapshot", definitive=True)
        try:
            with self.native_bridge_factory(current_process_identifier, self.timeout_seconds) as bridge:
                if bridge.trusted():
                    return bridge.dispatch_transport(operation, expected_project)
        except AdapterFailure:
            raise
        except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError):
            pass
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
            reason = str(result.get("reason") or "dispatch_failed")
            diagnostic = result.get("diagnostic")
            message = f"{reason}: {diagnostic}" if isinstance(diagnostic, str) and diagnostic else reason
            raise AdapterFailure(
                reason,
                message,
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
            "supported_transport_control_roles": list(SUPPORTED_TRANSPORT_CONTROL_ROLES),
            "capabilities": list(IMPLEMENTED_READS + IMPLEMENTED_WRITES),
            "operations": operations,
            "ui_languages": ["en", "ja"],
            "dispatch_contract": "one-semantic-operation",
            "readback_contract": "separate-observe-required",
        }

    def observe(self, operation: str) -> dict[str, Any]:
        if operation not in IMPLEMENTED_READS:
            raise AdapterFailure("unsupported_operation", f"reference adapter does not implement read: {operation}", definitive=True)
        snapshot = self.backend.snapshot(include_controls=operation == "transport.state")
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
                "process_identifier": snapshot.get("process_identifier"),
                "accessibility_backend": snapshot.get("accessibility_backend"),
                "screen_unlocked": snapshot.get("screen_unlocked"),
                "accessibility_authorized": snapshot.get("accessibility_authorized") is True,
                "modal_dialog": snapshot.get("modal_dialog") if snapshot.get("logic_running") is True else None,
                "frontmost": snapshot.get("frontmost"),
                "window_discovery_source": snapshot.get("window_discovery_source"),
                "window_discovery_diagnostic": snapshot.get("window_discovery_diagnostic"),
                "native_accessibility_diagnostic": snapshot.get("native_accessibility_diagnostic"),
                "window_set_complete": snapshot.get("window_set_complete") is True,
                "focus_temporarily_changed": snapshot.get("focus_temporarily_changed") is True,
                "transport_controls_observed": snapshot.get("transport_controls_observed") is True,
                "transport_controls_complete": snapshot.get("transport_controls_complete") is True,
                "capabilities": runtime_capabilities(snapshot),
            }
        elif operation == "project.current":
            data = project
            data["bundle_identifier"] = snapshot.get("bundle_identifier")
            data["process_identifier"] = snapshot.get("process_identifier")
            data["accessibility_backend"] = snapshot.get("accessibility_backend")
            data["window_discovery_source"] = snapshot.get("window_discovery_source")
            data["window_discovery_diagnostic"] = snapshot.get("window_discovery_diagnostic")
            data["native_accessibility_diagnostic"] = snapshot.get("native_accessibility_diagnostic")
            data["control_tree_diagnostic"] = snapshot.get("control_tree_diagnostic")
            data["window_set_complete"] = snapshot.get("window_set_complete") is True
            data["focus_temporarily_changed"] = snapshot.get("focus_temporarily_changed") is True
            data["transport_controls_observed"] = snapshot.get("transport_controls_observed") is True
            data["transport_controls_complete"] = snapshot.get("transport_controls_complete") is True
            data["capabilities"] = runtime_capabilities(snapshot)
        else:
            data = transport
            data.update(project)
            data["bundle_identifier"] = snapshot.get("bundle_identifier")
            data["process_identifier"] = snapshot.get("process_identifier")
            data["accessibility_backend"] = snapshot.get("accessibility_backend")
            data["window_discovery_source"] = snapshot.get("window_discovery_source")
            data["window_discovery_diagnostic"] = snapshot.get("window_discovery_diagnostic")
            data["native_accessibility_diagnostic"] = snapshot.get("native_accessibility_diagnostic")
            data["control_tree_diagnostic"] = snapshot.get("control_tree_diagnostic")
            data["window_set_complete"] = snapshot.get("window_set_complete") is True
            data["focus_temporarily_changed"] = snapshot.get("focus_temporarily_changed") is True
            data["transport_controls_observed"] = snapshot.get("transport_controls_observed") is True
            data["transport_controls_complete"] = snapshot.get("transport_controls_complete") is True
            data["capabilities"] = runtime_capabilities(snapshot)
        return {"ok": True, **common, "data": data}

    def dispatch(self, preflight: Any) -> dict[str, Any]:
        try:
            operation, request = validate_preflight(preflight)
            if operation not in IMPLEMENTED_WRITES:
                raise AdapterFailure("unsupported_operation", f"reference adapter does not implement write: {operation}", definitive=True)
            snapshot = self.backend.snapshot(include_controls=True)
            if snapshot.get("logic_running") is not True:
                raise AdapterFailure("logic_not_running", "Logic Pro is not running", definitive=True)
            if snapshot.get("screen_unlocked") is not True:
                raise AdapterFailure("screen_locked_or_unknown", "screen unlocked state is not confirmed", definitive=True)
            if snapshot.get("accessibility_authorized") is not True:
                raise AdapterFailure("accessibility_not_authorized", "Accessibility permission is not confirmed", definitive=True)
            if snapshot.get("window_set_complete") is not True:
                diagnostic = snapshot.get("window_discovery_diagnostic") or "cause_unavailable"
                raise AdapterFailure(
                    "window_set_incomplete",
                    f"complete Logic window set is not confirmed: {diagnostic}",
                    definitive=True,
                )
            if snapshot.get("modal_dialog") is not False:
                raise AdapterFailure("modal_dialog_present", "Logic modal state is unsafe or unknown", definitive=True)
            bundle_identifier = snapshot.get("bundle_identifier")
            if bundle_identifier not in SUPPORTED_LOGIC_BUNDLE_IDS:
                raise AdapterFailure("unsupported_logic_bundle", "running Logic bundle identifier is unsupported", definitive=True)
            expected_project = request["expected_project"]
            assert_project_binding(snapshot, expected_project)
            if snapshot.get("transport_controls_observed") is not True:
                raise AdapterFailure(
                    "transport_control_tree_unavailable",
                    "Logic main window does not expose a transport control tree",
                    definitive=True,
                )
            if snapshot.get("transport_controls_complete") is not True:
                raise AdapterFailure(
                    "transport_control_tree_truncated",
                    "Logic transport control tree exceeded the bounded traversal limit",
                    definitive=True,
                )
            capabilities = runtime_capabilities(snapshot)
            if operation not in capabilities or "transport.state" not in capabilities:
                raise AdapterFailure("capability_unavailable", "operation or independent readback is unavailable", definitive=True)
            action = self.backend.dispatch_transport(
                operation,
                expected_project,
                bundle_identifier,
                snapshot.get("process_identifier"),
            )
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
                    "window_discovery_source": action.get("window_discovery_source")
                    or snapshot.get("window_discovery_source"),
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
