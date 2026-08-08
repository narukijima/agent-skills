#!/usr/bin/env python3
"""Fail-closed preflight and outcome classification for Logic Pro operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any


READ_OPERATIONS = {
    "app.status",
    "project.current",
    "transport.state",
    "tracks.list",
    "track.selected",
    "regions.list",
    "instruments.list",
    "midi.ports",
}
WRITE_OPERATIONS = {
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
}
PROJECT_SCOPED = (READ_OPERATIONS - {"app.status", "project.current"}) | WRITE_OPERATIONS
NO_ARGUMENTS = {
    "app.status",
    "project.current",
    "transport.state",
    "tracks.list",
    "track.selected",
    "regions.list",
    "instruments.list",
    "midi.ports",
    "transport.play",
    "transport.stop",
    "project.save",
}
VERIFIED_SOURCES = {"logic-accessibility", "logic-mcp-state"}
VERIFY_CAPABILITIES = {
    "transport.play": "transport.state",
    "transport.stop": "transport.state",
    "transport.set_tempo": "transport.state",
    "transport.set_position": "transport.state",
    "track.select": "track.selected",
    "track.set_instrument": "tracks.list",
    "midi.import_file": "regions.list",
    "project.save": "project.current",
    "project.save_as": "project.current",
    "project.bounce": "project.current",
}


class GuardFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GuardFailure(message)


def read_json(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(value, dict), "input must be a JSON object")
    return value


def canonical(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def absolute_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value != "", f"{label} must be a non-empty string")
    require(os.path.isabs(value), f"{label} must be an absolute path")
    return canonical(value)


def under_root(path: str, roots: Any, label: str) -> None:
    require(isinstance(roots, list) and roots, f"{label} must contain at least one root")
    normalized_roots = []
    for root in roots:
        normalized_roots.append(absolute_path(root, label))
    require(
        any(os.path.commonpath([path, root]) == root for root in normalized_roots),
        f"path is outside {label}",
    )


def exact_keys(arguments: dict[str, Any], expected: set[str]) -> None:
    require(set(arguments) == expected, f"arguments must contain exactly: {', '.join(sorted(expected)) or 'none'}")


def validate_arguments(operation: str, arguments: dict[str, Any], environment: dict[str, Any], policy: dict[str, Any]) -> None:
    if operation in NO_ARGUMENTS:
        exact_keys(arguments, set())
        return
    if operation == "transport.set_tempo":
        exact_keys(arguments, {"tempo"})
        tempo = arguments["tempo"]
        require(isinstance(tempo, (int, float)) and not isinstance(tempo, bool), "tempo must be numeric")
        require(math.isfinite(float(tempo)) and 1 < float(tempo) <= 1000, "tempo must be finite and greater than 1 and at most 1000")
        return
    if operation == "transport.set_position":
        exact_keys(arguments, {"position"})
        position = arguments["position"]
        require(isinstance(position, str) and 0 < len(position) <= 128, "position must be a non-empty string up to 128 characters")
        return
    if operation == "track.select":
        require(set(arguments) in ({"track_id"}, {"track_index"}), "track.select requires exactly track_id or track_index")
        if "track_id" in arguments:
            require(isinstance(arguments["track_id"], str) and arguments["track_id"], "track_id must be non-empty")
            available = environment.get("available_track_ids")
            require(isinstance(available, list) and arguments["track_id"] in available, "track_id was not observed in available_track_ids")
        else:
            require(isinstance(arguments["track_index"], int) and not isinstance(arguments["track_index"], bool) and arguments["track_index"] >= 0, "track_index must be a non-negative integer")
            available = environment.get("available_track_indexes")
            require(isinstance(available, list) and arguments["track_index"] in available, "track_index was not observed in available_track_indexes")
        return
    if operation == "track.set_instrument":
        exact_keys(arguments, {"instrument_id", "track_id"})
        require(isinstance(arguments["track_id"], str) and arguments["track_id"], "track_id must be non-empty")
        tracks = environment.get("available_track_ids")
        require(isinstance(tracks, list) and arguments["track_id"] in tracks, "track_id was not observed in available_track_ids")
        instrument = arguments["instrument_id"]
        available = environment.get("available_instruments")
        require(isinstance(instrument, str) and instrument, "instrument_id must be non-empty")
        require(isinstance(available, list) and instrument in available, "instrument_id was not observed in available_instruments")
        return
    if operation == "midi.import_file":
        exact_keys(arguments, {"path"})
        path = absolute_path(arguments["path"], "MIDI path")
        under_root(path, policy.get("allowed_input_roots"), "allowed_input_roots")
        require(Path(path).is_file(), "MIDI path must be an existing regular file")
        require(Path(path).suffix.lower() in {".mid", ".midi"}, "MIDI path must end in .mid or .midi")
        return
    if operation in {"project.save_as", "project.bounce"}:
        exact_keys(arguments, {"path"})
        path = absolute_path(arguments["path"], "output path")
        under_root(path, policy.get("allowed_output_roots"), "allowed_output_roots")
        require(not Path(path).exists(), "refusing to overwrite an existing output path")
        require(Path(path).parent.is_dir(), "output parent must be an existing directory")
        extensions = {".logicx"} if operation == "project.save_as" else {".wav", ".aif", ".aiff", ".m4a", ".mp3"}
        require(Path(path).suffix.lower() in extensions, f"unsupported output extension for {operation}")
        return
    raise GuardFailure("operation has no argument contract")


def bind_project(environment: dict[str, Any]) -> str:
    expected = absolute_path(environment.get("expected_project"), "expected_project")
    require(Path(expected).suffix.lower() == ".logicx", "expected_project must end in .logicx")
    require(Path(expected).is_dir(), "expected_project must be an existing .logicx bundle")
    current = environment.get("current_project")
    if isinstance(current, str) and current:
        require(absolute_path(current, "current_project") == expected, "current_project does not match expected_project")
        return "project-url"
    require(environment.get("current_project_unavailable") is True, "current_project is missing without an explicit unavailable state")
    require(environment.get("window_project_name") == Path(expected).name, "window project name does not exactly match expected .logicx name")
    return "window-name-fallback"


def preflight(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    require(operation in READ_OPERATIONS | WRITE_OPERATIONS, "operation is not allowlisted")
    arguments = request.get("arguments", {})
    authorization = request.get("authorization", {})
    environment = request.get("environment", {})
    policy = request.get("policy", {})
    require(isinstance(arguments, dict), "arguments must be an object")
    require(isinstance(authorization, dict), "authorization must be an object")
    require(isinstance(environment, dict), "environment must be an object")
    require(isinstance(policy, dict), "policy must be an object")
    require(environment.get("logic_running") is True, "Logic Pro is not confirmed running")
    require(environment.get("screen_unlocked") is True, "screen is not confirmed unlocked")
    require(environment.get("accessibility_authorized") is True, "Accessibility permission is not confirmed")
    require(environment.get("modal_dialog") is False, "modal dialog state is unsafe or unknown")
    capabilities = environment.get("capabilities")
    require(isinstance(capabilities, list) and operation in capabilities, "operation is not present in observed capabilities")
    if operation in WRITE_OPERATIONS:
        require(authorization.get("write") is True, "write operation is not authorized by the current user request")
        verifier = VERIFY_CAPABILITIES[operation]
        require(verifier in capabilities, f"verification capability is missing: {verifier}")
    project_proof = "not-required"
    if operation in PROJECT_SCOPED:
        project_proof = bind_project(environment)
    validate_arguments(operation, arguments, environment, policy)
    stable = json.dumps(
        {"operation": operation, "arguments": arguments, "expected_project": environment.get("expected_project")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "ok": True,
        "classification": "authorized",
        "operation": operation,
        "impact": "write" if operation in WRITE_OPERATIONS else "read",
        "project_proof": project_proof,
        "request_sha256": hashlib.sha256(stable).hexdigest(),
    }


def classify(result: dict[str, Any]) -> dict[str, Any]:
    dispatch = result.get("dispatch")
    readback = result.get("readback")
    require(isinstance(dispatch, dict), "dispatch must be an object")
    status = dispatch.get("status")
    require(status in {"success", "failed", "unknown"}, "dispatch.status must be success, failed, or unknown")
    if status == "failed" and dispatch.get("definitive") is True:
        return {"ok": False, "class": "C", "classification": "confirmed_failure", "retry": "not-automatic", "gui_fallback_eligible": True}
    if status == "success" and isinstance(readback, dict):
        verified = (
            readback.get("fresh") is True
            and readback.get("source") in VERIFIED_SOURCES
            and readback.get("matches_expected") is True
        )
        if verified:
            return {"ok": True, "class": "A", "classification": "verified_success", "retry": "not-needed", "gui_fallback_eligible": False}
    return {"ok": False, "class": "B", "classification": "unknown", "retry": "forbidden-until-state-is-resolved", "gui_fallback_eligible": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Logic Pro operation requests and classify outcomes.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    before = subparsers.add_parser("preflight", help="validate a single operation request without executing it")
    before.add_argument("--request", required=True, help="JSON file, or - for stdin")
    after = subparsers.add_parser("classify", help="classify dispatch and independent readback evidence")
    after.add_argument("--result", required=True, help="JSON file, or - for stdin")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = read_json(args.request if args.command == "preflight" else args.result)
        output = preflight(source) if args.command == "preflight" else classify(source)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except (GuardFailure, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"ok": False, "classification": "rejected", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
