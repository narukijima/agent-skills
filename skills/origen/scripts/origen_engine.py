#!/usr/bin/env python3
"""Current Policy-enforced Origen engine and atomic publication bundles."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import origen_atomic


VERSION = "0.4.0"
SCHEMA_VERSION = "origen-evidence/4"
CONFIG_VERSION = "origen-config/1"
POLICY_VERSION = "origen-trust-policy/2"
REGISTRY_VERSION = "origen-provider-registry/1"
OPERATION_VERSION = "origen-operation/1"
SOURCE_MAP_VERSIONS = {"origen-source-map/2"}
POLICY_MODES = {"development", "production"}
SIGNER_ROLES = {"root-attestor", "final-attestor"}
ROOT_AUTHORIZATION_TYPES = {
    "trusted_ingest", "explicit_authorization", "pre_authorized_workflow",
    "trusted_capture_service", "hardware_backed_authorization", "provider_authorization",
}
BUILTIN_MEDIA_TYPES = {"text/plain", "text/markdown", "application/json", "image/png"}
UNSUPPORTED_FINAL_MEDIA_TYPES = {"application/octet-stream", "application/zip"}
TEXT_MEDIA_TYPES = {"text/plain", "text/markdown"}
CONTENT_SIGNAL_STATES = {"unknown", "not_detected", "detected"}
FORBIDDEN_COMMAND_FLAGS = {"--sign-command", "--verify-command", "--adapter-command", "--inspector-command"}
ALLOWED_SEPARATORS = {"", " ", "\n", "\n\n", "\t"}
ALLOWED_OPERATIONS = {
    "identity", "crop", "resize", "rotate", "trim", "concat", "resample",
    "gain", "channel-map", "mux", "overlay-signed-asset",
    "render-signed-text", "add-signed-subtitle",
}
CONTENT_BEARING_OPS = {"overlay-signed-asset", "render-signed-text", "add-signed-subtitle"}
UNSAFE_PARAMETER_KEYS = {
    "url", "uri", "base64", "binary", "shell", "command", "mask", "network",
    "content", "text", "image", "logo", "subtitle",
}
OPERATION_PARAMETERS = {
    "identity": set(),
    "crop": {"x", "y", "width", "height"},
    "resize": {"width", "height", "filter"},
    "rotate": {"degrees"},
    "trim": {"start", "end"},
    "concat": {"source_ids"},
    "resample": {"rate"},
    "gain": {"db"},
    "channel-map": {"channels"},
    "mux": {"source_ids"},
    "overlay-signed-asset": {"source_id", "x", "y", "opacity"},
    "render-signed-text": {"source_id", "font_resource_id", "x", "y"},
    "add-signed-subtitle": {"source_id", "start", "end", "style_resource_id"},
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DEFAULT_LIMITS: dict[str, int | float] = {
    "input_file_bytes": 64 * 1024 * 1024,
    "output_file_bytes": 64 * 1024 * 1024,
    "decoded_bytes": 256 * 1024 * 1024,
    "pixel_count": 40_000_000,
    "width": 16_384,
    "height": 16_384,
    "frame_count": 10_000,
    "duration_seconds": 14_400,
    "sample_count": 2_000_000_000,
    "source_count": 128,
    "operation_count": 10_000,
    "source_map_bytes": 4 * 1024 * 1024,
    "json_depth": 64,
    "archive_entry_count": 10_000,
    "compression_ratio": 200.0,
    "subprocess_timeout_seconds": 60,
    "subprocess_stdout_bytes": 1024 * 1024,
    "subprocess_stderr_bytes": 1024 * 1024,
}

FINAL_COVERAGE = {
    "file_type", "container_validity", "mime_extension_consistency", "metadata",
    "c2pa", "exif_xmp_iptc", "active_content", "embedded_files",
    "external_references", "decodability", "resource_limits", "policy_coverage",
}

CONFIG_FIELDS = {
    "schema_version", "root_signer", "final_signer", "timestamp_provider",
    "provider_registry", "policy",
}
POLICY_FIELDS = {
    "policy_id", "policy_version", "mode", "root_required",
    "human_origin_claim", "allowed_media_types", "resource_limits", "environment_policy",
    "publisher_handoff_policy", "slice_boundary_policy", "c2pa_policy",
    "publication_profiles", "approved_json_schemas", "external_manifest_policy",
}
TOOL_POLICY_FIELDS = {
    "executable", "arguments", "expected_executable_sha256", "expected_script_sha256",
    "expected_resource_sha256", "provider", "version", "dependency_provenance",
    "reproducible_install", "role", "key_id", "algorithm", "signer_identity",
    "builder_identity", "inspector_identity", "provider_id", "provider_identity",
    "verifier", "root_authorization", "inherit_environment", "protocol",
}

EVIDENCE_FIELDS = {
    "schema_version", "operation_schema_version", "evidence_type", "created_at",
    "policy", "asset", "input_asset", "origin", "event", "assurance", "actors",
    "identities", "toolchain", "timestamp", "lineage", "source_mapping",
    "publication", "authorization", "publish_ready", "proof",
}

DEFAULT_POLICY: dict[str, object] = {
    "policy_id": "origen-default",
    "policy_version": "1.0.0",
    "mode": "production",
    "root_required": True,
    "human_origin_claim": True,
    "allowed_media_types": ["text/plain", "text/markdown", "application/json", "image/png"],
    "resource_limits": {},
    "environment_policy": {"network": "deny", "approved_path": [], "allowed_variables": {}},
    "publisher_handoff_policy": {
        "publication_representations": ["canonical-bytes"],
        "allowed_transport_metadata": ["content-type"],
    },
    "slice_boundary_policy": {
        "allowed": ["grapheme", "token", "word", "line", "paragraph"],
        "advanced_code_point": False,
        "allow_letter_synthesis": False,
    },
    "c2pa_policy": {"action": "detach"},
    "publication_profiles": {
        "markdown-safe": {"front_matter": "forbid", "raw_html": "forbid", "comments": "forbid"},
    },
    "approved_json_schemas": {},
    "external_manifest_policy": "reject-unless-approved-inspector",
}


class OrigenError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_depth(value: object, maximum: int, depth: int = 0) -> None:
    if depth > maximum:
        raise OrigenError("JSON_DEPTH_EXCEEDED", "JSON nesting exceeds Policy limit", maximum=maximum)
    if isinstance(value, dict):
        for item in value.values():
            validate_depth(item, maximum, depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_depth(item, maximum, depth + 1)


def load_strict_json_bytes(data: bytes, core: Any, *, label: str, max_depth: int) -> dict[str, object]:
    try:
        text = data.decode("utf-8", errors="strict")
        value = core.load_strict_json(text)
    except UnicodeDecodeError as error:
        raise OrigenError("INVALID_JSON", f"{label} is not strict UTF-8 JSON") from error
    except core.OrigenError as error:
        raise OrigenError(error.code, error.message, **error.details) from error
    if not isinstance(value, dict):
        raise OrigenError("INVALID_JSON", f"{label} root must be an object")
    validate_depth(value, max_depth)
    return value


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str
    size: int
    name_hint: str

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


class SnapshotStore:
    """Private content-addressed staging populated from already-open file descriptors."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="origen-snapshots-")
        self.root = Path(self._temporary.name)
        os.chmod(self.root, 0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(mode=0o700)

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "SnapshotStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _copy_fd(self, fd: int, *, label: str, maximum: int, name_hint: str) -> Snapshot:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise OrigenError("NOT_REGULAR_FILE", f"{label} must be a regular file")
        if before.st_size > maximum:
            raise OrigenError("FILE_TOO_LARGE", f"{label} exceeds Policy byte limit", size=before.st_size, maximum=maximum)
        temporary_fd, temporary_name = tempfile.mkstemp(prefix="copy-", dir=self.objects)
        digest = hashlib.sha256()
        copied = 0
        try:
            with os.fdopen(temporary_fd, "wb", closefd=True) as output:
                while True:
                    chunk = os.read(fd, min(1024 * 1024, maximum - copied + 1))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > maximum:
                        raise OrigenError("FILE_TOO_LARGE", f"{label} exceeds Policy byte limit", maximum=maximum)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(fd)
            stable = (
                before.st_dev, before.st_ino, before.st_size,
                getattr(before, "st_mtime_ns", int(before.st_mtime * 1e9)),
                getattr(before, "st_ctime_ns", int(before.st_ctime * 1e9)),
            ) == (
                after.st_dev, after.st_ino, after.st_size,
                getattr(after, "st_mtime_ns", int(after.st_mtime * 1e9)),
                getattr(after, "st_ctime_ns", int(after.st_ctime * 1e9)),
            )
            if not stable or copied != before.st_size:
                raise OrigenError("INPUT_MUTATED", f"{label} changed while the secure snapshot was captured")
            hexdigest = digest.hexdigest()
            destination = self.objects / hexdigest
            if destination.exists():
                Path(temporary_name).unlink()
            else:
                os.rename(temporary_name, destination)
                os.chmod(destination, 0o400)
                directory_fd = os.open(self.objects, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return Snapshot(destination, hexdigest, copied, name_hint)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def capture(self, path: Path, *, label: str, maximum: int) -> Snapshot:
        try:
            info = os.lstat(path)
        except FileNotFoundError as error:
            raise OrigenError("FILE_NOT_FOUND", f"{label} does not exist", path=str(path)) from error
        if stat.S_ISLNK(info.st_mode):
            raise OrigenError("SYMLINK_REJECTED", f"{label} must not be a symlink", path=str(path))
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as error:
            raise OrigenError("SECURE_OPEN_FAILED", f"{label} could not be opened without following links", path=str(path)) from error
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise OrigenError("PATH_SWAPPED", f"{label} path changed between lstat and no-follow open", path=str(path))
            return self._copy_fd(fd, label=label, maximum=maximum, name_hint=path.name)
        finally:
            os.close(fd)

    def capture_child(self, directory: Path, name: str, *, label: str, maximum: int) -> Snapshot:
        try:
            info = os.lstat(directory)
        except FileNotFoundError as error:
            raise OrigenError("BUNDLE_NOT_FOUND", "publish bundle does not exist", path=str(directory)) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OrigenError("INVALID_BUNDLE", "publish bundle must be a non-symlink directory")
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        dir_fd = os.open(directory, dir_flags)
        try:
            entries = set(os.listdir(dir_fd))
            if entries != {"asset", "evidence.json", "receipt.json"}:
                raise OrigenError("INVALID_BUNDLE", "publish bundle must contain exactly asset, evidence.json, and receipt.json", entries=sorted(entries))
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, dir_fd=dir_fd)
            except OSError as error:
                raise OrigenError("INVALID_BUNDLE", f"bundle {name} cannot be opened safely") from error
            try:
                return self._copy_fd(fd, label=label, maximum=maximum, name_hint=name)
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)


def timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OrigenError("INVALID_TIMESTAMP", f"{label} must be a non-empty RFC3339 timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise OrigenError("INVALID_TIMESTAMP", f"{label} must be RFC3339-compatible") from error
    if parsed.tzinfo is None:
        raise OrigenError("INVALID_TIMESTAMP", f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_string(mapping: dict[str, object], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise OrigenError("INVALID_POLICY", f"{context}.{key} must be a non-empty string")
    return value


def require_sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise OrigenError("INVALID_POLICY", f"{context} must be a lowercase SHA-256 digest")
    return value


def policy_limits(policy: dict[str, object]) -> dict[str, int | float]:
    supplied = policy.get("resource_limits")
    if not isinstance(supplied, dict):
        raise OrigenError("INVALID_POLICY", "resource_limits must be an object")
    unknown = set(supplied) - set(DEFAULT_LIMITS)
    if unknown:
        raise OrigenError("UNKNOWN_POLICY_FIELD", "unknown resource limit", fields=sorted(unknown))
    limits = dict(DEFAULT_LIMITS)
    for key, value in supplied.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise OrigenError("INVALID_POLICY", f"resource_limits.{key} must be positive")
        limits[key] = value
    return limits


def validate_policy(policy: dict[str, object]) -> None:
    unknown = set(policy) - POLICY_FIELDS
    if unknown:
        raise OrigenError("UNKNOWN_POLICY_FIELD", "Trust Policy contains unknown critical fields", fields=sorted(unknown))
    require_string(policy, "policy_id", "policy")
    require_string(policy, "policy_version", "policy")
    mode = policy.get("mode")
    if mode not in POLICY_MODES:
        raise OrigenError("INVALID_POLICY", "mode must be development or production")
    root_required = policy.get("root_required", True)
    if not isinstance(root_required, bool):
        raise OrigenError("INVALID_POLICY", "root_required must be boolean")
    human_claim = policy.get("human_origin_claim", root_required)
    if not isinstance(human_claim, bool):
        raise OrigenError("INVALID_POLICY", "human_origin_claim must be boolean")
    if mode == "production" and not root_required and human_claim:
        raise OrigenError("INVALID_POLICY", "rootless Production profile must set human_origin_claim=false")
    media = policy.get("allowed_media_types")
    if not isinstance(media, list) or not media or any(not isinstance(item, str) or not item for item in media):
        raise OrigenError("INVALID_POLICY", "allowed_media_types must be a non-empty string list")
    policy_limits(policy)
    environment = policy.get("environment_policy")
    if not isinstance(environment, dict) or environment.get("network") not in {"deny", "explicit"}:
        raise OrigenError("INVALID_POLICY", "environment_policy.network must be deny or explicit")
    approved_path = environment.get("approved_path")
    if not isinstance(approved_path, list) or any(not isinstance(item, str) or not Path(item).is_absolute() for item in approved_path):
        raise OrigenError("INVALID_POLICY", "environment_policy.approved_path must contain absolute directories")
    handoff = policy.get("publisher_handoff_policy")
    if not isinstance(handoff, dict):
        raise OrigenError("INVALID_POLICY", "publisher_handoff_policy must be an object")
    representations = handoff.get("publication_representations")
    if not isinstance(representations, list) or not representations or any(not isinstance(item, str) for item in representations):
        raise OrigenError("INVALID_POLICY", "publisher_handoff_policy.publication_representations is required")
    if not isinstance(handoff.get("allowed_transport_metadata", []), list):
        raise OrigenError("INVALID_POLICY", "allowed_transport_metadata must be a list")
    slice_policy = policy.get("slice_boundary_policy", {})
    if not isinstance(slice_policy, dict):
        raise OrigenError("INVALID_POLICY", "slice_boundary_policy must be an object")
    c2pa_policy = policy.get("c2pa_policy", {})
    if not isinstance(c2pa_policy, dict) or c2pa_policy.get("action", "detach") not in {"preserve", "reissue", "detach"}:
        raise OrigenError("INVALID_POLICY", "c2pa_policy.action must be preserve, reissue, or detach")
    for field in ("publication_profiles", "approved_json_schemas"):
        if not isinstance(policy.get(field, {}), dict):
            raise OrigenError("INVALID_POLICY", f"{field} must be an object")


def merged_policy(config: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    supplied = config.get("policy", {})
    if not isinstance(supplied, dict):
        raise OrigenError("INVALID_CONFIG", "config.policy must be an object")
    unknown = set(supplied) - POLICY_FIELDS
    if unknown:
        raise OrigenError("UNKNOWN_POLICY_FIELD", "Trust Policy contains unknown critical fields", fields=sorted(unknown))
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    for key, value in supplied.items():
        policy[key] = value
    validate_policy(policy)
    policy_document = {
        "schema_version": POLICY_VERSION,
        "root_signer": config["root_signer"],
        "final_signer": config["final_signer"],
        "timestamp_provider": config["timestamp_provider"],
        **policy,
    }
    return policy, policy_document


def load_config(snapshot: Snapshot, core: Any) -> dict[str, object]:
    config = load_strict_json_bytes(snapshot.read_bytes(), core, label="Origen config", max_depth=int(DEFAULT_LIMITS["json_depth"]))
    unknown = set(config) - CONFIG_FIELDS
    if unknown:
        raise OrigenError("UNKNOWN_CONFIG_FIELD", "Origen config contains unknown critical fields", fields=sorted(unknown))
    if config.get("schema_version") != CONFIG_VERSION:
        raise OrigenError("UNSUPPORTED_CONFIG_SCHEMA", "unsupported Origen config schema")
    for field in ("root_signer", "final_signer", "timestamp_provider"):
        require_string(config, field, "config")
    if config["root_signer"] == config["final_signer"]:
        raise OrigenError("ROLE_ALIAS_COLLISION", "root_signer and final_signer must be distinct logical aliases")
    registry = config.get("provider_registry", "providers.json")
    if not isinstance(registry, str) or not registry:
        raise OrigenError("INVALID_CONFIG", "provider_registry must be a non-empty path")
    if not isinstance(config.get("policy", {}), dict):
        raise OrigenError("INVALID_CONFIG", "policy must be an object")
    return config


def load_registry(snapshot: Snapshot, core: Any) -> dict[str, object]:
    registry = load_strict_json_bytes(snapshot.read_bytes(), core, label="Provider registry", max_depth=int(DEFAULT_LIMITS["json_depth"]))
    expected = {"schema_version", "providers", "signers", "timestamp_providers", "builders", "inspectors"}
    unknown = set(registry) - expected
    if unknown:
        raise OrigenError("UNKNOWN_REGISTRY_FIELD", "Provider registry contains unknown critical fields", fields=sorted(unknown))
    if registry.get("schema_version") != REGISTRY_VERSION:
        raise OrigenError("UNSUPPORTED_REGISTRY_SCHEMA", "unsupported Provider registry schema")
    for field in ("providers", "signers", "timestamp_providers", "builders", "inspectors"):
        value = registry.get(field, {})
        if not isinstance(value, dict):
            raise OrigenError("INVALID_REGISTRY", f"{field} must be an object")
    return registry


def merge_provider_entry(registry: dict[str, object], alias: str, category: str) -> dict[str, object]:
    aliases = registry.get(category, {})
    if not isinstance(aliases, dict) or alias not in aliases or not isinstance(aliases[alias], dict):
        raise OrigenError("INVALID_REGISTRY", f"{category}.{alias} is not defined")
    specific = dict(aliases[alias])
    provider_id = specific.get("provider")
    providers = registry.get("providers", {})
    if not isinstance(provider_id, str) or not isinstance(providers, dict) or provider_id not in providers or not isinstance(providers[provider_id], dict):
        raise OrigenError("PROVIDER_NOT_FOUND", f"{category}.{alias} references an unknown provider")
    entry = {**providers[provider_id], **specific, "provider_id": provider_id}
    entry.pop("provider", None)
    entry.setdefault("provider_identity", providers[provider_id].get("provider_identity", provider_id))
    if category == "signers":
        entry.setdefault("algorithm", "Ed25519")
        if entry["algorithm"] != "Ed25519":
            raise OrigenError("ALGORITHM_NOT_SUPPORTED", "Origen v4 default signer contract requires Ed25519")
        require_string(entry, "key_id", f"signers.{alias}")
        require_string(entry, "signer_identity", f"signers.{alias}")
        verifier = entry.get("verifier")
        if not isinstance(verifier, dict) or set(verifier) - {"public_key", "verifier_ref"} or not any(isinstance(verifier.get(key), str) and verifier.get(key) for key in ("public_key", "verifier_ref")):
            raise OrigenError("VERIFIER_REFERENCE_REQUIRED", f"signers.{alias} must expose public_key or verifier_ref")
    return entry


def hash_regular_nofollow(path: Path, *, label: str) -> tuple[str, os.stat_result]:
    if not path.is_absolute():
        raise OrigenError("POLICY_PATH_NOT_ABSOLUTE", f"{label} path must be absolute", path=str(path))
    try:
        info = os.lstat(path)
    except FileNotFoundError as error:
        raise OrigenError("TOOL_NOT_FOUND", f"{label} does not exist", path=str(path)) from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OrigenError("UNSAFE_TOOL_PATH", f"{label} must be a regular non-symlink file", path=str(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise OrigenError("TOOL_PATH_RACE", f"{label} changed before hashing")
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OrigenError("TOOL_PATH_RACE", f"{label} changed while hashing")
        return digest.hexdigest(), after
    finally:
        os.close(fd)


def resolve_tool(policy: dict[str, object], category: str, tool_id: str | None, *, role: str | None = None) -> dict[str, object]:
    if not tool_id:
        raise OrigenError("APPROVED_ID_REQUIRED", f"an approved {category} ID is required")
    registry = policy.get(category)
    if not isinstance(registry, dict) or tool_id not in registry:
        code = {
            "approved_signers": "SIGNER_NOT_APPROVED",
            "approved_builders": "BUILDER_NOT_APPROVED",
            "approved_inspectors": "INSPECTOR_NOT_APPROVED",
            "approved_timestamp_providers": "TIMESTAMP_PROVIDER_NOT_APPROVED",
        }[category]
        raise OrigenError(code, f"{tool_id!r} is not approved by this Trust Policy")
    raw = registry[tool_id]
    if not isinstance(raw, dict):
        raise OrigenError("INVALID_POLICY", f"{category}.{tool_id} must be an object")
    entry = dict(raw)
    unknown = set(entry) - TOOL_POLICY_FIELDS
    if unknown:
        raise OrigenError("UNKNOWN_POLICY_FIELD", f"{category}.{tool_id} contains unknown critical fields", fields=sorted(unknown))
    executable = Path(require_string(entry, "executable", f"{category}.{tool_id}"))
    expected_executable = require_sha256(entry.get("expected_executable_sha256"), f"{category}.{tool_id}.expected_executable_sha256")
    actual_executable, executable_stat = hash_regular_nofollow(executable, label=f"{category} executable")
    if actual_executable != expected_executable:
        raise OrigenError("EXECUTABLE_HASH_MISMATCH", "approved executable hash does not match Policy", tool_id=tool_id)
    if role is not None:
        entry["role"] = role
    arguments = entry.get("arguments", [])
    if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
        raise OrigenError("INVALID_POLICY", f"{category}.{tool_id}.arguments must be a string list")
    script_hashes = entry.get("expected_script_sha256", {})
    resource_hashes = entry.get("expected_resource_sha256", {})
    if not isinstance(script_hashes, dict) or not isinstance(resource_hashes, dict):
        raise OrigenError("INVALID_POLICY", "expected script/resource hashes must be path-to-digest objects")
    verified_files = []
    for kind, values in (("script", script_hashes), ("resource", resource_hashes)):
        for raw_path, expected in values.items():
            if not isinstance(raw_path, str):
                raise OrigenError("INVALID_POLICY", f"expected_{kind}_sha256 keys must be paths")
            expected_digest = require_sha256(expected, f"expected_{kind}_sha256[{raw_path}]")
            actual, _ = hash_regular_nofollow(Path(raw_path), label=f"tool {kind}")
            if actual != expected_digest:
                raise OrigenError(f"{kind.upper()}_HASH_MISMATCH", f"approved {kind} hash does not match Policy", path=raw_path)
            verified_files.append({"kind": kind, "path": raw_path, "sha256": actual})
    for argument in arguments:
        candidate = Path(argument)
        if candidate.is_absolute() and candidate.exists():
            if str(candidate) not in script_hashes and str(candidate) not in resource_hashes:
                raise OrigenError("UNPINNED_TOOL_ARGUMENT", "existing absolute command file is not hash-pinned", path=str(candidate))
    if category == "approved_signers":
        require_string(entry, "key_id", f"{category}.{tool_id}")
        algorithm = require_string(entry, "algorithm", f"{category}.{tool_id}")
        require_string(entry, "signer_identity", f"{category}.{tool_id}")
        if algorithm != "Ed25519":
            raise OrigenError("ALGORITHM_NOT_SUPPORTED", "Origen v4 requires Ed25519 signer entries")
        verifier = entry.get("verifier")
        if not isinstance(verifier, dict) or not any(isinstance(verifier.get(key), str) and verifier.get(key) for key in ("public_key", "verifier_ref")):
            raise OrigenError("VERIFIER_REFERENCE_REQUIRED", "signer entry must expose public verifier information")
        if role == "root-attestor":
            authorization = entry.get("root_authorization")
            accepted = authorization.get("accepted_boundaries") if isinstance(authorization, dict) else None
            if not isinstance(accepted, list) or not accepted or any(item not in ROOT_AUTHORIZATION_TYPES for item in accepted):
                raise OrigenError("ROOT_AUTHORIZATION_POLICY_REQUIRED", "root signer must declare accepted authorization boundaries")
    entry.update({
        "id": tool_id,
        "executable": str(executable),
        "argv": [str(executable), *arguments],
        "actual_executable_sha256": actual_executable,
        "verified_files": verified_files,
        "executable_stat": (executable_stat.st_dev, executable_stat.st_ino, executable_stat.st_mtime_ns, executable_stat.st_ctime_ns),
    })
    return entry


def tool_claim(entry: dict[str, object], *, identity_field: str) -> dict[str, object]:
    claim: dict[str, object] = {
        "id": entry["id"],
        "identity": entry.get(identity_field, entry.get("provider", entry["id"])),
        "version": entry.get("version", "policy-pinned"),
        "executable_sha256": entry["actual_executable_sha256"],
        "script_hashes": [item for item in entry["verified_files"] if item["kind"] == "script"],
        "resource_hashes": [item for item in entry["verified_files"] if item["kind"] == "resource"],
        "dependency_provenance": entry.get("dependency_provenance", "policy-pinned external tool"),
        "reproducible_install": entry.get("reproducible_install", "see Trust Policy deployment record"),
    }
    if "role" in entry:
        claim["role"] = entry["role"]
    if "key_id" in entry:
        claim["key_id"] = entry["key_id"]
        claim["algorithm"] = entry["algorithm"]
    return claim


def sanitized_environment(policy: dict[str, object], workdir: Path, entry: dict[str, object] | None = None) -> dict[str, str]:
    environment = policy["environment_policy"]
    approved_path = environment.get("approved_path", [])
    env = {
        "PATH": os.pathsep.join(approved_path),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": str(workdir),
        "ORIGEN_NETWORK_POLICY": str(environment["network"]),
    }
    allowed = environment.get("allowed_variables", {})
    if not isinstance(allowed, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in allowed.items()):
        raise OrigenError("INVALID_POLICY", "environment_policy.allowed_variables must be a literal string map")
    env.update(allowed)
    inherited = entry.get("inherit_environment", []) if entry else []
    if not isinstance(inherited, list) or any(not isinstance(name, str) or not name for name in inherited):
        raise OrigenError("INVALID_REGISTRY", "inherit_environment must be a list of variable names")
    for name in inherited:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def run_tool(entry: dict[str, object], request: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], operation: str, workdir: Path) -> dict[str, object]:
    payload = canonical_bytes(request)
    stdin_file = tempfile.TemporaryFile(dir=workdir)
    stdin_file.write(payload)
    stdin_file.seek(0)
    process = subprocess.Popen(
        entry["argv"], stdin=stdin_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=workdir, env=sanitized_environment(policy, workdir, entry), close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    for stream in (process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    caps = {
        "stdout": int(limits["subprocess_stdout_bytes"]),
        "stderr": int(limits["subprocess_stderr_bytes"]),
    }
    deadline = time.monotonic() + float(limits["subprocess_timeout_seconds"])
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise OrigenError("PROVIDER_TIMEOUT", f"{operation} exceeded the Policy timeout")
            events = selector.select(min(remaining, 0.2))
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in list(selector.get_map().values())]
            for key, _ in events:
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = captured[key.data]
                target.extend(chunk)
                if len(target) > caps[key.data]:
                    process.kill()
                    process.wait()
                    raise OrigenError("PROVIDER_OUTPUT_LIMIT", f"{operation} exceeded {key.data} byte limit")
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        selector.close()
        stdin_file.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    if returncode != 0:
        raise OrigenError("PROVIDER_FAILED", f"{operation} returned non-zero", returncode=returncode)
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} response contains duplicate keys", key=key)
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} response contains NaN or Infinity", value=value)

    try:
        response = json.loads(
            bytes(captured["stdout"]).decode("utf-8"),
            object_pairs_hook=unique_pairs, parse_constant=reject_constant,
        )
    except OrigenError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} did not return one JSON object") from error
    if not isinstance(response, dict):
        raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} response must be an object")
    actual_after, after_stat = hash_regular_nofollow(Path(entry["executable"]), label=f"{operation} executable")
    if actual_after != entry["actual_executable_sha256"] or (
        after_stat.st_dev, after_stat.st_ino, after_stat.st_mtime_ns, after_stat.st_ctime_ns
    ) != entry["executable_stat"]:
        raise OrigenError("TOOL_MUTATED_DURING_EXECUTION", f"{operation} executable changed during execution")
    return response


def signer_identity(entry: dict[str, object]) -> dict[str, object]:
    return {
        "signer_id": entry["id"], "role": entry["role"], "key_id": entry["key_id"],
        "identity": entry["signer_identity"], "algorithm": entry["algorithm"],
        "provider_id": entry["provider_id"], "verifier": entry["verifier"],
    }


def sign_statement(statement: dict[str, object], signer: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> dict[str, object]:
    payload = canonical_bytes(statement)
    response = run_tool(signer, {
        "operation": "sign", "protocol": "origen-signer/1", "role": signer["role"],
        "key_id": signer["key_id"], "algorithm": signer["algorithm"],
        "payload": base64.b64encode(payload).decode("ascii"), "payload_sha256": digest_bytes(payload),
    }, policy=policy, limits=limits, operation="sign", workdir=workdir)
    for key, expected in (("provider_id", signer["provider_id"]), ("key_id", signer["key_id"]), ("algorithm", signer["algorithm"]), ("signer_identity", signer["signer_identity"])):
        if response.get(key) != expected:
            raise OrigenError("SIGNER_IDENTITY_MISMATCH", f"signer returned an unexpected {key}")
    signature = response.get("signature")
    if not isinstance(signature, str) or not signature:
        raise OrigenError("INVALID_PROVIDER_RESPONSE", "signer response is missing signature")
    if signer["role"] == "root-attestor" and policy["mode"] == "production":
        authorization = response.get("authorization_receipt_digest")
        signed_authorization = statement.get("authorization", {}).get("receipt_digest")
        if not isinstance(authorization, str) or not SHA256_RE.fullmatch(authorization) or authorization != signed_authorization:
            raise OrigenError("ROOT_AUTHORIZATION_REQUIRED", "root-attestor did not echo the signed out-of-band authorization receipt digest")
    return {"signature": signature}


def authorize_root(subject: dict[str, object], signer: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> tuple[dict[str, object], dict[str, object]]:
    subject_digest = digest_bytes(canonical_bytes(subject))
    response = run_tool(signer, {
        "operation": "authorize_root", "protocol": "origen-root-authorization/2",
        "subject_sha256": subject_digest,
        "policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
    }, policy=policy, limits=limits, operation="root-authorization", workdir=workdir)
    boundary_type = response.get("boundary_type")
    accepted = signer["root_authorization"]["accepted_boundaries"]
    if boundary_type not in accepted:
        raise OrigenError("ROOT_AUTHORIZATION_REJECTED", "provider used an authorization boundary not accepted by the root signer", boundary_type=boundary_type)
    boundary_id = response.get("boundary_id")
    receipt = response.get("receipt")
    if not isinstance(boundary_id, str) or not boundary_id or not isinstance(receipt, str) or not receipt:
        raise OrigenError("ROOT_AUTHORIZATION_REQUIRED", "root authorization must return boundary_id and receipt")
    if response.get("subject_sha256") != subject_digest:
        raise OrigenError("ROOT_AUTHORIZATION_SUBJECT_MISMATCH", "authorization receipt is not bound to this Human Source subject")
    receipt_digest = digest_bytes(receipt.encode("utf-8"))
    statement = {
        "boundary_type": boundary_type,
        "boundary_id": boundary_id,
        "provider_id": signer["provider_id"],
        "provider_identity": signer["provider_identity"],
        "subject_sha256": subject_digest,
        "receipt_digest": receipt_digest,
    }
    return statement, {"authorization_receipt": receipt}


def verify_statement(evidence: dict[str, object], verifier: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> None:
    proof = evidence.get("proof")
    if not isinstance(proof, dict) or not isinstance(proof.get("signature"), str):
        raise OrigenError("INVALID_EVIDENCE", "v4 proof must contain a signature")
    statement = {key: value for key, value in evidence.items() if key != "proof"}
    response = run_tool(verifier, {
        "operation": "verify", "protocol": "origen-signer/1",
        "key_id": evidence["identities"]["signer"]["key_id"],
        "algorithm": evidence["identities"]["signer"]["algorithm"],
        "payload": base64.b64encode(canonical_bytes(statement)).decode("ascii"),
        "payload_sha256": digest_bytes(canonical_bytes(statement)), "signature": proof["signature"],
        "verifier": evidence["identities"]["signer"]["verifier"],
    }, policy=policy, limits=limits, operation="verify", workdir=workdir)
    if response.get("verified") is not True:
        raise OrigenError("SIGNATURE_INVALID", "v4 evidence signature was not verified")
    signed = evidence["identities"]["signer"]
    if response.get("provider_id") != signed.get("provider_id"):
        raise OrigenError("SIGNATURE_IDENTITY_MISMATCH", "verifier returned a different provider_id")
    for key in ("key_id", "algorithm", "identity"):
        response_key = "signer_identity" if key == "identity" else key
        if response.get(response_key) != signed.get(key):
            raise OrigenError("SIGNATURE_IDENTITY_MISMATCH", f"verifier returned a different {key}")


def verify_root_authorization(evidence: dict[str, object], signer: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> None:
    authorization = evidence.get("authorization")
    proof = evidence.get("proof")
    if not isinstance(authorization, dict) or not isinstance(proof, dict):
        raise OrigenError("ROOT_AUTHORIZATION_REQUIRED", "Human Root authorization evidence is missing")
    receipt = proof.get("authorization_receipt")
    if not isinstance(receipt, str) or digest_bytes(receipt.encode("utf-8")) != authorization.get("receipt_digest"):
        raise OrigenError("ROOT_AUTHORIZATION_TAMPERED", "authorization receipt does not match its signed digest")
    if authorization.get("boundary_type") not in signer["root_authorization"]["accepted_boundaries"]:
        raise OrigenError("ROOT_AUTHORIZATION_REJECTED", "signed authorization boundary is no longer accepted")
    if authorization.get("provider_id") != signer["provider_id"] or authorization.get("provider_identity") != signer["provider_identity"]:
        raise OrigenError("ROOT_AUTHORIZATION_PROVIDER_MISMATCH", "authorization provider differs from the signed root signer provider")
    response = run_tool(signer, {
        "operation": "verify_authorization", "protocol": "origen-root-authorization/2",
        "boundary_type": authorization.get("boundary_type"),
        "boundary_id": authorization.get("boundary_id"),
        "subject_sha256": authorization.get("subject_sha256"),
        "receipt": receipt,
    }, policy=policy, limits=limits, operation="verify-root-authorization", workdir=workdir)
    if response.get("verified") is not True:
        raise OrigenError("ROOT_AUTHORIZATION_INVALID", "Human Root authorization receipt was not verified")


def obtain_trusted_timestamp(subject: dict[str, object], provider: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> tuple[dict[str, object], dict[str, object]]:
    subject_digest = digest_bytes(canonical_bytes(subject))
    response = run_tool(provider, {
        "operation": "timestamp", "protocol": "origen-trusted-time/1",
        "subject_sha256": subject_digest,
    }, policy=policy, limits=limits, operation="timestamp", workdir=workdir)
    trusted_time = normalize_timestamp(response.get("trusted_time"), label="trusted_time")
    receipt = response.get("receipt")
    if not isinstance(receipt, str) or not receipt:
        raise OrigenError("INVALID_TIMESTAMP_RESPONSE", "timestamp provider response is missing receipt")
    identity = response.get("provider_identity")
    if response.get("provider_id") != provider["provider_id"] or identity != provider["provider_identity"]:
        raise OrigenError("TIMESTAMP_PROVIDER_MISMATCH", "timestamp provider identity does not match Policy")
    receipt_digest = digest_bytes(receipt.encode("utf-8"))
    statement = {
        "provider_id": provider["id"], "provider_runtime_id": provider["provider_id"], "provider_identity": identity,
        "protocol": response.get("protocol", "RFC3161-or-equivalent"),
        "subject_sha256": subject_digest, "trusted_time": trusted_time,
        "receipt_digest": receipt_digest,
    }
    proof = {"timestamp_receipt": receipt}
    return statement, proof


def verify_trusted_timestamp(evidence: dict[str, object], provider: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path) -> None:
    timestamp = evidence.get("timestamp")
    proof = evidence.get("proof")
    if not isinstance(timestamp, dict) or not isinstance(proof, dict):
        raise OrigenError("TRUSTED_TIMESTAMP_REQUIRED", "trusted timestamp statement and receipt are required")
    if timestamp.get("provider_id") != provider["id"] or timestamp.get("provider_runtime_id") != provider["provider_id"] or timestamp.get("provider_identity") != provider["provider_identity"]:
        raise OrigenError("TIMESTAMP_PROVIDER_MISMATCH", "signed timestamp provider identity differs from the configured verifier")
    receipt = proof.get("timestamp_receipt")
    if not isinstance(receipt, str) or digest_bytes(receipt.encode("utf-8")) != timestamp.get("receipt_digest"):
        raise OrigenError("TIMESTAMP_RECEIPT_TAMPERED", "timestamp receipt digest does not match signed statement")
    response = run_tool(provider, {
        "operation": "verify_timestamp", "protocol": "origen-trusted-time/1",
        "subject_sha256": timestamp.get("subject_sha256"), "trusted_time": timestamp.get("trusted_time"),
        "receipt": receipt,
    }, policy=policy, limits=limits, operation="verify-timestamp", workdir=workdir)
    if response.get("verified") is not True:
        raise OrigenError("TIMESTAMP_INVALID", "trusted timestamp receipt was not verified")


def detect_c2pa_markers(snapshot: Snapshot, media_type: str, core: Any) -> list[str]:
    data = snapshot.read_bytes()
    lowered = data.lower()
    markers: set[str] = set()
    if media_type == "image/png":
        try:
            for kind, _ in core.parse_png(data):
                if kind == b"caBX":
                    markers.add("PNG-caBX")
        except core.OrigenError as error:
            raise OrigenError(error.code, error.message, **error.details) from error
    if media_type == "image/jpeg" and (b"jumb" in lowered or b"c2pa" in lowered):
        markers.add("JPEG-JUMBF-APP11")
    if data.startswith(b"RIFF") and b"c2pa" in data[12:]:
        markers.add("RIFF-C2PA")
    if data.startswith(b"ID3") and b"GEOB" in data and b"c2pa" in lowered:
        markers.add("ID3-GEOB-C2PA")
    if len(data) >= 12 and data[4:8] == b"ftyp" and (b"c2pa" in lowered or b"jumb" in lowered):
        markers.add("ISO-BMFF-C2PA-JUMBF")
    if media_type == "application/pdf" and b"/embeddedfile" in lowered and (b"c2pa" in lowered or b"jumb" in lowered):
        markers.add("PDF-embedded-C2PA")
    if media_type in {"text/plain", "text/markdown", "text/html", "image/svg+xml"}:
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise OrigenError("INVALID_UTF8", "text asset is not strict UTF-8") from error
        start = "-----BEGIN C2PA MANIFEST-----" in text
        end = "-----END C2PA MANIFEST-----" in text
        if start != end:
            raise OrigenError("MALFORMED_C2PA_TEXT_WRAPPER", "C2PA text wrapper is incomplete")
        if start:
            markers.add("TEXT-structured-C2PA")
        if text.startswith("\ufeff"):
            markers.add("TEXT-U+FEFF-prefix")
        if any("\ufe00" <= char <= "\ufe0f" or "\U000e0100" <= char <= "\U000e01ef" for char in text):
            markers.add("TEXT-variation-selector")
        if "C2PATXT" in text:
            markers.add("TEXT-C2PATXT")
        lower_text = text.lower()
        if media_type == "text/html":
            if re.search(r"<script\b[^>]*type\s*=\s*['\"]application/c2pa['\"]", lower_text):
                markers.add("HTML-inline-C2PA")
            if re.search(r"<link\b[^>]*rel\s*=\s*['\"]c2pa-manifest['\"]", lower_text):
                markers.add("HTML-external-C2PA")
        if media_type == "image/svg+xml" and "c2pa:manifest" in lower_text:
            markers.add("SVG-C2PA-manifest")
    if snapshot.name_hint.lower().endswith(".c2pa"):
        markers.add("external-C2PA-manifest")
    return sorted(markers)


def markdown_checks(snapshot: Snapshot, profile: dict[str, object]) -> list[dict[str, str]]:
    text = snapshot.read_bytes().decode("utf-8", errors="strict")
    findings: list[dict[str, str]] = []
    if profile.get("front_matter") == "forbid" and (text.startswith("---\n") or text.startswith("+++\n")):
        findings.append({"code": "MARKDOWN_FRONT_MATTER", "message": "front matter is forbidden by publication profile"})
    if profile.get("raw_html") == "forbid" and re.search(r"<\/?[A-Za-z][^>]*>", text):
        findings.append({"code": "MARKDOWN_RAW_HTML", "message": "raw HTML is forbidden by publication profile"})
    if profile.get("comments") == "forbid" and "<!--" in text:
        findings.append({"code": "MARKDOWN_COMMENT", "message": "HTML comments are forbidden by publication profile"})
    return findings


def enforce_png_limits(snapshot: Snapshot, limits: dict[str, int | float], core: Any) -> None:
    data = snapshot.read_bytes()
    chunks = core.parse_png(data)
    header = chunks[0][1]
    width, height, bit_depth, channels = core.validate_png_header(header)
    if width > limits["width"] or height > limits["height"]:
        raise OrigenError("PNG_DIMENSIONS_EXCEEDED", "PNG dimensions exceed Policy limits", width=width, height=height)
    if width * height > limits["pixel_count"]:
        raise OrigenError("PNG_PIXEL_LIMIT", "PNG pixel count exceeds Policy limit", pixels=width * height)
    row_bytes = (width * channels * bit_depth + 7) // 8
    decoded = height * (row_bytes + 1)
    if decoded > limits["decoded_bytes"]:
        raise OrigenError("PNG_DECOMPRESSION_LIMIT", "PNG decoded bytes exceed Policy limit", decoded_bytes=decoded)


def inspect_snapshot(snapshot: Snapshot, *, policy: dict[str, object], limits: dict[str, int | float], core: Any, publication_profile: str | None = None) -> dict[str, object]:
    try:
        inspection = core.inspect_asset(snapshot.path, name_hint=snapshot.name_hint)
    except core.OrigenError as error:
        raise OrigenError(error.code, error.message, **error.details) from error
    media_type = inspection["asset"]["media_type"]
    if media_type not in policy["allowed_media_types"]:
        raise OrigenError("MEDIA_TYPE_NOT_ALLOWED", "media type is not allowed by Policy", media_type=media_type)
    if media_type == "image/png":
        enforce_png_limits(snapshot, limits, core)
    if media_type == "application/zip":
        try:
            with zipfile.ZipFile(snapshot.path) as archive:
                entries = archive.infolist()
        except (zipfile.BadZipFile, OSError) as error:
            raise OrigenError("INVALID_ARCHIVE", "ZIP container is malformed") from error
        if len(entries) > limits["archive_entry_count"]:
            raise OrigenError("ARCHIVE_ENTRY_LIMIT", "ZIP entry count exceeds Policy limit")
        compressed = sum(max(item.compress_size, 1) for item in entries)
        uncompressed = sum(item.file_size for item in entries)
        if uncompressed > limits["decoded_bytes"] or (uncompressed / compressed if compressed else float("inf")) > limits["compression_ratio"]:
            raise OrigenError("ARCHIVE_BOMB", "ZIP expansion exceeds Policy limits")
    markers = detect_c2pa_markers(snapshot, media_type, core)
    findings = list(inspection.get("findings", []))
    if media_type == "text/markdown":
        profiles = policy.get("publication_profiles", {})
        if not publication_profile or not isinstance(profiles, dict) or publication_profile not in profiles:
            raise OrigenError("PUBLICATION_PROFILE_REQUIRED", "Markdown requires a Policy publication profile")
        profile = profiles[publication_profile]
        if not isinstance(profile, dict):
            raise OrigenError("INVALID_POLICY", "Markdown publication profile must be an object")
        findings.extend(markdown_checks(snapshot, profile))
    return {**inspection, "findings": findings, "c2pa_markers": markers}


def validate_typed_operation(value: object, *, source_ids: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - {"op", "parameters"}:
        raise OrigenError("INVALID_OPERATION", "typed operation must contain only op and parameters")
    op = value.get("op")
    parameters = value.get("parameters", {})
    if op not in ALLOWED_OPERATIONS or not isinstance(parameters, dict):
        raise OrigenError("INVALID_OPERATION", "operation is not allowed by v1 typed schema", operation=op)
    for key, item in parameters.items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in UNSAFE_PARAMETER_KEYS):
            raise OrigenError("UNSAFE_OPERATION_PARAMETER", "operation parameter is forbidden", parameter=key)
        if isinstance(item, str) and (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", item) or "base64," in item.lower()):
            raise OrigenError("UNSAFE_OPERATION_PARAMETER", "URL/base64 content is forbidden in operations", parameter=key)
    allowed_parameters = OPERATION_PARAMETERS[op]
    if set(parameters) - allowed_parameters:
        raise OrigenError("INVALID_OPERATION", "operation contains parameters outside its typed schema", parameters=sorted(set(parameters) - allowed_parameters))
    required: dict[str, set[str]] = {
        "crop": {"x", "y", "width", "height"}, "resize": {"width", "height"},
        "rotate": {"degrees"}, "trim": {"start", "end"}, "concat": {"source_ids"},
        "resample": {"rate"}, "gain": {"db"}, "channel-map": {"channels"},
        "mux": {"source_ids"}, "overlay-signed-asset": {"source_id"},
        "render-signed-text": {"source_id", "font_resource_id"},
        "add-signed-subtitle": {"source_id", "start", "end"},
    }
    missing = required.get(op, set()) - set(parameters)
    if missing:
        raise OrigenError("INVALID_OPERATION", "operation is missing required typed parameters", parameters=sorted(missing))
    canonical_bytes(parameters)
    for key, item in parameters.items():
        if isinstance(item, str):
            if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", item) or "base64," in item.lower():
                raise OrigenError("UNSAFE_OPERATION_PARAMETER", "URL/base64 content is forbidden in operations", parameter=key)
            if len(item) > 1024:
                raise OrigenError("UNSAFE_OPERATION_PARAMETER", "freeform content is forbidden in operations", parameter=key)
        elif not isinstance(item, (int, float, bool, list, dict, type(None))):
            raise OrigenError("INVALID_OPERATION", "operation parameter has unsupported type", parameter=key)
    if op in CONTENT_BEARING_OPS:
        source_id = parameters.get("source_id")
        if source_id not in source_ids:
            raise OrigenError("UNSIGNED_CONTENT_RESOURCE", f"{op} requires a signed source_id")
    for key in ("width", "height", "rate"):
        if key in parameters and (not isinstance(parameters[key], (int, float)) or isinstance(parameters[key], bool) or parameters[key] <= 0):
            raise OrigenError("INVALID_OPERATION", f"{op}.{key} must be positive")
    for key in ("x", "y", "start", "end"):
        if key in parameters and (not isinstance(parameters[key], (int, float)) or isinstance(parameters[key], bool) or parameters[key] < 0):
            raise OrigenError("INVALID_OPERATION", f"{op}.{key} must be non-negative")
    if "start" in parameters and "end" in parameters and parameters["end"] <= parameters["start"]:
        raise OrigenError("INVALID_OPERATION", f"{op} end must be greater than start")
    for key in ("source_ids", "channels"):
        if key in parameters and (not isinstance(parameters[key], list) or not parameters[key]):
            raise OrigenError("INVALID_OPERATION", f"{op}.{key} must be a non-empty list")
    if "source_ids" in parameters and any(item not in source_ids for item in parameters["source_ids"]):
        raise OrigenError("UNSIGNED_CONTENT_RESOURCE", f"{op} source_ids must reference signed sources")
    return {"op": op, "parameters": parameters}


def text_boundaries(text: str) -> dict[str, set[int]]:
    grapheme = {0, len(text)}
    for index in range(1, len(text)):
        previous, current = text[index - 1], text[index]
        if not unicodedata.category(current).startswith("M") and previous != "\u200d" and current != "\u200d" and not ("\ufe00" <= current <= "\ufe0f" or "\U000e0100" <= current <= "\U000e01ef"):
            grapheme.add(index)
    word = {0, len(text)} | {match.start() for match in re.finditer(r"\b", text)}
    line = {0, len(text)} | {index + 1 for index, char in enumerate(text) if char == "\n"}
    paragraph = {0, len(text)} | {match.start() for match in re.finditer(r"\n\n+", text)} | {match.end() for match in re.finditer(r"\n\n+", text)}
    return {"grapheme": grapheme, "token": word, "word": word, "line": line, "paragraph": paragraph}


def validate_asset_record(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"id", "sha256", "size", "media_type"}:
        raise OrigenError("INVALID_EVIDENCE", f"{context} has unknown or missing fields")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest) or value.get("id") != f"sha256:{digest}":
        raise OrigenError("INVALID_EVIDENCE", f"{context} hash identity is invalid")
    if not isinstance(value.get("size"), int) or value["size"] < 0 or not isinstance(value.get("media_type"), str):
        raise OrigenError("INVALID_EVIDENCE", f"{context} size/media_type is invalid")
    return value


def asset_record(snapshot: Snapshot, inspection: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"sha256:{snapshot.sha256}", "sha256": snapshot.sha256, "size": snapshot.size,
        "media_type": inspection["asset"]["media_type"],
    }


def evidence_digest(evidence: dict[str, object]) -> str:
    return digest_bytes(canonical_bytes(evidence))


def validate_evidence(evidence: dict[str, object]) -> None:
    unknown = set(evidence) - EVIDENCE_FIELDS
    if unknown:
        raise OrigenError("UNKNOWN_EVIDENCE_FIELD", "evidence contains unknown critical fields", fields=sorted(unknown))
    if evidence.get("schema_version") != SCHEMA_VERSION or evidence.get("operation_schema_version") != OPERATION_VERSION:
        raise OrigenError("UNSUPPORTED_EVIDENCE_SCHEMA", "Production requires Origen evidence v4")
    evidence_type = evidence.get("evidence_type")
    if evidence_type not in {"human-root", "final-asset"}:
        raise OrigenError("INVALID_EVIDENCE", "evidence_type is invalid")
    normalize_timestamp(evidence.get("created_at"), label="created_at")
    validate_asset_record(evidence.get("asset"), "asset")
    policy = evidence.get("policy")
    if not isinstance(policy, dict) or set(policy) != {"policy_id", "policy_version", "mode", "digest"}:
        raise OrigenError("INVALID_EVIDENCE", "signed Policy claim is malformed")
    require_sha256(policy.get("digest"), "evidence.policy.digest")
    identities = evidence.get("identities")
    if not isinstance(identities, dict) or set(identities) != {"signer"} or not isinstance(identities.get("signer"), dict):
        raise OrigenError("INVALID_EVIDENCE", "signed signer identity is incomplete")
    signer = identities["signer"]
    if set(signer) != {"signer_id", "role", "provider_id", "key_id", "identity", "algorithm", "verifier"}:
        raise OrigenError("INVALID_EVIDENCE", "signed signer identity has unknown or missing fields")
    if signer.get("role") not in SIGNER_ROLES:
        raise OrigenError("INVALID_EVIDENCE", "signed attestor role is invalid")
    if signer.get("algorithm") != "Ed25519" or not isinstance(signer.get("verifier"), dict):
        raise OrigenError("INVALID_EVIDENCE", "signed algorithm or verifier reference is invalid")
    assurance = evidence.get("assurance")
    if not isinstance(assurance, dict) or set(assurance) != {"structural", "content_signals", "derivation", "root"}:
        raise OrigenError("INVALID_EVIDENCE", "assurance dimensions are incomplete")
    for key in ("structural", "content_signals", "derivation", "root"):
        if not isinstance(assurance.get(key), dict):
            raise OrigenError("INVALID_EVIDENCE", f"assurance.{key} must be an object")
    if assurance["content_signals"].get("state") not in CONTENT_SIGNAL_STATES:
        raise OrigenError("INVALID_EVIDENCE", "content signal state is invalid")
    proof = evidence.get("proof")
    if not isinstance(proof, dict) or not isinstance(proof.get("signature"), str):
        raise OrigenError("INVALID_EVIDENCE", "proof signature is missing")
    allowed_proof = {"signature", "authorization_receipt", "timestamp_receipt"}
    if set(proof) - allowed_proof:
        raise OrigenError("UNKNOWN_EVIDENCE_FIELD", "proof contains unknown critical fields", fields=sorted(set(proof) - allowed_proof))
    if evidence_type == "human-root":
        if signer.get("role") != "root-attestor" or evidence.get("publish_ready") is not False:
            raise OrigenError("INVALID_EVIDENCE", "Human Root requires root-attestor and publish_ready=false")
        if not isinstance(evidence.get("origin"), dict) or not isinstance(evidence.get("timestamp"), dict):
            raise OrigenError("INVALID_EVIDENCE", "Human Root origin/timestamp claims are missing")
        event = evidence.get("event")
        if not isinstance(event, dict) or set(event) != {"action", "tool", "version"} or event.get("action") != "human-root-attestation":
            raise OrigenError("INVALID_EVIDENCE", "Human Root event claim is malformed")
        authorization = evidence.get("authorization")
        required_auth = {"boundary_type", "boundary_id", "provider_id", "provider_identity", "subject_sha256", "receipt_digest"}
        if not isinstance(authorization, dict) or set(authorization) != required_auth:
            raise OrigenError("INVALID_EVIDENCE", "Human Root authorization evidence is malformed")
        if authorization.get("boundary_type") not in ROOT_AUTHORIZATION_TYPES:
            raise OrigenError("INVALID_EVIDENCE", "Human Root authorization boundary type is invalid")
        require_sha256(authorization.get("subject_sha256"), "authorization.subject_sha256")
        require_sha256(authorization.get("receipt_digest"), "authorization.receipt_digest")
        if assurance["root"].get("assurance_level") not in {"signed_assertion", "trusted_time", "capture_attested"}:
            raise OrigenError("INVALID_EVIDENCE", "root assurance level is invalid")
    else:
        if signer.get("role") != "final-attestor" or evidence.get("publish_ready") is not True:
            raise OrigenError("INVALID_EVIDENCE", "Final evidence requires final-attestor and publish_ready=true")
        validate_asset_record(evidence.get("input_asset"), "input_asset")
        event = evidence.get("event")
        if not isinstance(event, dict) or set(event) != {"action", "source_kind", "transformations", "tool", "version"} or event.get("action") != "trusted-finalization":
            raise OrigenError("INVALID_EVIDENCE", "Final event claim is malformed")
        if assurance["structural"].get("state") != "clean":
            raise OrigenError("INVALID_EVIDENCE", "Final structural assurance must be clean")
        mode = assurance["derivation"].get("mode")
        no_unmapped = assurance["derivation"].get("no_unmapped_generated_content")
        if mode == "standard" and no_unmapped is not False:
            raise OrigenError("INVALID_EVIDENCE", "STANDARD cannot claim no unmapped generated content")
        if mode == "strict_origin" and no_unmapped is not True:
            raise OrigenError("INVALID_EVIDENCE", "STRICT ORIGIN must sign its mapping guarantee")
        if mode not in {"standard", "strict_origin"}:
            raise OrigenError("INVALID_EVIDENCE", "derivation mode is invalid")
        if mode == "strict_origin" and not isinstance(evidence.get("source_mapping"), dict):
            raise OrigenError("INVALID_EVIDENCE", "STRICT ORIGIN source_mapping is required")
        if mode == "standard" and evidence.get("source_mapping") is not None:
            raise OrigenError("INVALID_EVIDENCE", "STANDARD must not contain a Strict source mapping")
        if evidence.get("authorization") is not None:
            raise OrigenError("INVALID_EVIDENCE", "Final evidence must not contain Human Root authorization")


def load_evidence(snapshot: Snapshot, core: Any, limits: dict[str, int | float]) -> dict[str, object]:
    evidence = load_strict_json_bytes(snapshot.read_bytes(), core, label="Evidence", max_depth=int(limits["json_depth"]))
    validate_evidence(evidence)
    return evidence


def policy_claim(policy: dict[str, object], digest: str) -> dict[str, object]:
    return {
        "policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
        "mode": policy["mode"], "digest": digest,
    }


def verify_policy_claim(evidence: dict[str, object], policy: dict[str, object], policy_digest: str) -> None:
    signed_policy = evidence.get("policy")
    if policy["mode"] == "production" and isinstance(signed_policy, dict) and signed_policy.get("mode") != "production":
        raise OrigenError("DEVELOPMENT_EVIDENCE_REJECTED", "development evidence cannot pass Production prepublish")
    expected = policy_claim(policy, policy_digest)
    if signed_policy != expected:
        raise OrigenError("POLICY_DIGEST_MISMATCH", "evidence was not created under this exact Trust Policy")


def resolve_evidence_signer(policy: dict[str, object], evidence: dict[str, object]) -> dict[str, object]:
    signed = evidence["identities"]["signer"]
    signer = resolve_tool(policy, "approved_signers", signed.get("signer_id"), role=signed.get("role"))
    expected = signer_identity(signer)
    if canonical_bytes(expected) != canonical_bytes(signed):
        raise OrigenError("SIGNER_REGISTRY_MISMATCH", "Provider registry no longer contains the signed key identity; retain rotated verifier entries")
    return signer


def verify_evidence_snapshot(snapshot: Snapshot, *, policy: dict[str, object], policy_digest: str, verifier: dict[str, object] | None, timestamp_provider_id: str | None, store: SnapshotStore, limits: dict[str, int | float], core: Any) -> dict[str, object]:
    evidence = load_evidence(snapshot, core, limits)
    verify_policy_claim(evidence, policy, policy_digest)
    signer = resolve_evidence_signer(policy, evidence)
    with tempfile.TemporaryDirectory(prefix="verify-", dir=store.root) as work:
        verify_statement(evidence, signer, policy=policy, limits=limits, workdir=Path(work))
        if evidence["evidence_type"] == "human-root":
            verify_root_authorization(evidence, signer, policy=policy, limits=limits, workdir=Path(work))
        if evidence["evidence_type"] == "human-root" and evidence["assurance"]["root"]["assurance_level"] in {"trusted_time", "capture_attested"}:
            provider_id = timestamp_provider_id or evidence["timestamp"].get("provider_id")
            provider = resolve_tool(policy, "approved_timestamp_providers", provider_id)
            verify_trusted_timestamp(evidence, provider, policy=policy, limits=limits, workdir=Path(work))
    return evidence


def secure_source_path(base: Path, raw: object) -> Path:
    if not isinstance(raw, str) or not raw:
        raise OrigenError("INVALID_SOURCE_MAP", "source path must be a non-empty string")
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else base / candidate


def compose_source_map(map_snapshot: Snapshot, *, map_original_path: Path, policy: dict[str, object], policy_digest: str, verifier: dict[str, object], timestamp_provider_id: str | None, root_evidence: dict[str, object], store: SnapshotStore, limits: dict[str, int | float], core: Any) -> dict[str, object]:
    raw = load_strict_json_bytes(map_snapshot.read_bytes(), core, label="source map", max_depth=int(limits["json_depth"]))
    if raw.get("schema_version") not in SOURCE_MAP_VERSIONS or raw.get("kind") not in {"text", "media"}:
        raise OrigenError("INVALID_SOURCE_MAP", "unsupported source map schema or kind")
    source_values = raw.get("sources")
    if not isinstance(source_values, list) or not source_values:
        raise OrigenError("SOURCE_MAP_INCOMPLETE", "source map requires signed Human sources")
    if len(source_values) > limits["source_count"]:
        raise OrigenError("SOURCE_COUNT_EXCEEDED", "source count exceeds Policy limit")
    sources: dict[str, dict[str, object]] = {}
    summaries = []
    base = map_original_path.parent
    for item in source_values:
        if not isinstance(item, dict) or set(item) != {"source_id", "asset", "evidence"}:
            raise OrigenError("INVALID_SOURCE_MAP", "source entries have unknown or missing fields")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id) or source_id in sources:
            raise OrigenError("INVALID_SOURCE_MAP", "source_id must be unique and portable")
        source_snapshot = store.capture(secure_source_path(base, item.get("asset")), label=f"source asset {source_id}", maximum=int(limits["input_file_bytes"]))
        evidence_snapshot = store.capture(secure_source_path(base, item.get("evidence")), label=f"source evidence {source_id}", maximum=int(limits["source_map_bytes"]))
        source_evidence = verify_evidence_snapshot(evidence_snapshot, policy=policy, policy_digest=policy_digest, verifier=verifier, timestamp_provider_id=timestamp_provider_id, store=store, limits=limits, core=core)
        if source_evidence["evidence_type"] != "human-root":
            raise OrigenError("STRICT_SOURCE_NOT_HUMAN", "Strict sources must use Human Root evidence")
        expected = validate_asset_record(source_evidence["asset"], f"source[{source_id}].asset")
        if source_snapshot.sha256 != expected["sha256"] or source_snapshot.size != expected["size"]:
            raise OrigenError("SOURCE_ASSET_MISMATCH", "source snapshot does not match signed Human Root", source_id=source_id)
        summary = {"source_id": source_id, "asset_id": expected["id"], "evidence_digest": evidence_digest(source_evidence)}
        sources[source_id] = {"snapshot": source_snapshot, "evidence": source_evidence, "summary": summary}
        summaries.append(summary)
    root_digest = evidence_digest(root_evidence)
    if not any(item["summary"]["evidence_digest"] == root_digest and item["summary"]["asset_id"] == root_evidence["asset"]["id"] for item in sources.values()):
        raise OrigenError("SOURCE_MAP_ROOT_MISSING", "source map must include the supplied Human Root")
    summary: dict[str, object] = {
        "schema_version": raw["schema_version"], "kind": raw["kind"], "sources": summaries,
        "instruction_actor": raw.get("instruction_actor", "tool"),
    }
    actor = summary["instruction_actor"]
    if actor not in {"ai", "human", "tool", "mixed"}:
        raise OrigenError("INVALID_SOURCE_MAP", "instruction_actor is invalid")
    assembled: bytes | None = None
    operations_out: list[dict[str, object]] = []
    if raw["kind"] == "text":
        operations = raw.get("operations")
        if not isinstance(operations, list) or not operations:
            raise OrigenError("SOURCE_MAP_INCOMPLETE", "text source map requires operations")
        if len(operations) > limits["operation_count"]:
            raise OrigenError("OPERATION_COUNT_EXCEEDED", "source map operation count exceeds Policy limit")
        allowed_boundaries = policy.get("slice_boundary_policy", {}).get("allowed", ["grapheme", "token", "word", "line", "paragraph"])
        if not isinstance(allowed_boundaries, list) or any(item not in {"grapheme", "token", "word", "line", "paragraph", "code_point"} for item in allowed_boundaries):
            raise OrigenError("INVALID_POLICY", "slice boundary policy is invalid")
        if "code_point" in allowed_boundaries and not policy.get("slice_boundary_policy", {}).get("advanced_code_point", False):
            raise OrigenError("INVALID_POLICY", "code_point slicing requires advanced_code_point=true")
        normalized: dict[str, str] = {}
        boundaries: dict[str, dict[str, set[int]]] = {}
        for source_id, source in sources.items():
            if source["evidence"]["asset"]["media_type"] not in TEXT_MEDIA_TYPES:
                raise OrigenError("STRICT_TEXT_SOURCE_UNSUPPORTED", "Strict text sources must be TXT or Markdown")
            try:
                text = source["snapshot"].read_bytes().decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise OrigenError("INVALID_UTF8", "Strict text source is not UTF-8") from error
            try:
                text, _ = core.normalize_text(text)
            except core.OrigenError as error:
                raise OrigenError(error.code, error.message, **error.details) from error
            normalized[source_id] = text
            boundaries[source_id] = text_boundaries(text)
        pieces: list[str] = []
        one_grapheme_slices = 0
        for operation in operations:
            if not isinstance(operation, dict):
                raise OrigenError("INVALID_SOURCE_MAP", "source map operation must be an object")
            if operation.get("op") == "separator":
                if set(operation) != {"op", "value"} or operation.get("value") not in ALLOWED_SEPARATORS:
                    raise OrigenError("STRICT_LITERAL_FORBIDDEN", "only fixed whitespace separators are allowed")
                pieces.append(operation["value"])
                operations_out.append({"op": "separator", "value": operation["value"]})
                continue
            if operation.get("op") != "slice" or set(operation) - {"op", "source_id", "start", "end", "boundary"}:
                raise OrigenError("INVALID_SOURCE_MAP", "unsupported text operation")
            source_id, start, end = operation.get("source_id"), operation.get("start"), operation.get("end")
            boundary = operation.get("boundary", "grapheme")
            if source_id not in normalized or not isinstance(start, int) or not isinstance(end, int) or boundary not in allowed_boundaries:
                raise OrigenError("INVALID_SOURCE_MAP", "slice source/bounds/boundary are invalid")
            source_text = normalized[source_id]
            if start < 0 or end <= start or end > len(source_text):
                raise OrigenError("SOURCE_MAP_RANGE_INVALID", "slice bounds are outside normalized source")
            if boundary != "code_point" and (start not in boundaries[source_id][boundary] or end not in boundaries[source_id][boundary]):
                raise OrigenError("SLICE_BOUNDARY_VIOLATION", "slice splits the declared boundary", boundary=boundary)
            if len([i for i in boundaries[source_id]["grapheme"] if start < i <= end]) == 1:
                one_grapheme_slices += 1
            pieces.append(source_text[start:end])
            operations_out.append({"op": "slice", "source_id": source_id, "start": start, "end": end, "boundary": boundary})
        if one_grapheme_slices > 1 and not policy.get("slice_boundary_policy", {}).get("allow_letter_synthesis", False):
            raise OrigenError("LETTER_SYNTHESIS_FORBIDDEN", "letter-by-letter semantic synthesis is forbidden by default")
        assembled = core.canonical_text_output("".join(pieces))
        summary["operations"] = operations_out
        summary["rebuilt_output_sha256"] = digest_bytes(assembled)
    else:
        primary = raw.get("primary_source_id")
        if primary not in sources:
            raise OrigenError("SOURCE_MAP_INCOMPLETE", "media map primary_source_id is invalid")
        operation = validate_typed_operation(raw.get("transformation"), source_ids=set(sources))
        summary["primary_source_id"] = primary
        summary["transformation"] = operation
    summary["source_map_digest"] = map_snapshot.sha256
    summary["mapping_digest"] = digest_bytes(canonical_bytes(summary))
    return {"kind": raw["kind"], "sources": sources, "summary": summary, "assembled": assembled, "primary_source_id": raw.get("primary_source_id"), "transformation": summary.get("transformation")}


def validate_json_shape(value: object, schema: dict[str, object], *, path: str = "$") -> None:
    expected = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str, "number": (int, float),
        "integer": int, "boolean": bool, "null": type(None),
    }
    if expected not in types or not isinstance(value, types[expected]) or (expected in {"number", "integer"} and isinstance(value, bool)):
        raise OrigenError("JSON_SCHEMA_MISMATCH", "JSON value does not match pinned shape", path=path, expected=expected)
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additional_properties", False)
        if not isinstance(properties, dict) or not isinstance(required, list) or not isinstance(additional, bool):
            raise OrigenError("INVALID_JSON_SCHEMA", "origen-json-shape/1 object schema is malformed")
        if any(key not in value for key in required):
            raise OrigenError("JSON_SCHEMA_MISMATCH", "JSON required property is missing", path=path)
        if not additional and set(value) - set(properties):
            raise OrigenError("JSON_SCHEMA_MISMATCH", "JSON contains an unapproved property", path=path, properties=sorted(set(value) - set(properties)))
        for key, item in value.items():
            if key in properties:
                validate_json_shape(item, properties[key], path=f"{path}.{key}")
    elif expected == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise OrigenError("INVALID_JSON_SCHEMA", "array schema requires items")
        for index, item in enumerate(value):
            validate_json_shape(item, items, path=f"{path}[{index}]")


def validate_json_with_policy(snapshot: Snapshot, schema_id: str | None, *, policy: dict[str, object], store: SnapshotStore, limits: dict[str, int | float], core: Any) -> dict[str, object]:
    schemas = policy.get("approved_json_schemas", {})
    if not schema_id or not isinstance(schemas, dict) or schema_id not in schemas:
        raise OrigenError("JSON_SCHEMA_REQUIRED", "built-in JSON finalization requires an approved schema ID")
    descriptor = schemas[schema_id]
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise OrigenError("INVALID_POLICY", "approved_json_schemas entry is malformed")
    schema_snapshot = store.capture(Path(str(descriptor["path"])), label="JSON shape schema", maximum=int(limits["source_map_bytes"]))
    if schema_snapshot.sha256 != require_sha256(descriptor["sha256"], "approved JSON schema hash"):
        raise OrigenError("RESOURCE_HASH_MISMATCH", "JSON shape schema hash does not match Policy")
    schema = load_strict_json_bytes(schema_snapshot.read_bytes(), core, label="JSON shape schema", max_depth=int(limits["json_depth"]))
    if schema.get("schema_version") != "origen-json-shape/1":
        raise OrigenError("INVALID_JSON_SCHEMA", "only origen-json-shape/1 is built in")
    try:
        value = core.load_strict_json(snapshot.read_bytes().decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, core.OrigenError) as error:
        raise OrigenError("INVALID_JSON", "JSON input is invalid") from error
    shape = schema.get("shape")
    if not isinstance(shape, dict):
        raise OrigenError("INVALID_JSON_SCHEMA", "origen-json-shape/1 requires an object shape")
    validate_json_shape(value, shape, path="$")
    return {"schema_id": schema_id, "schema_sha256": schema_snapshot.sha256}


def builtin_toolchain(core: Any, *, schema_resource: dict[str, object] | None = None) -> dict[str, object]:
    runtime = Path(os.path.realpath(sys.executable))
    executable_hash, _ = hash_regular_nofollow(runtime, label="Python runtime")
    script_claims = []
    for path in (Path(__file__).with_name("origen.py"), Path(core.__file__), Path(__file__)):
        digest, _ = hash_regular_nofollow(path, label="Origen script")
        script_claims.append({"path": path.name, "sha256": digest})
    resources = [schema_resource] if schema_resource else []
    return {
        "id": "origen/builtin", "identity": "origen/builtin-0.3", "version": VERSION,
        "executable_sha256": executable_hash, "script_hashes": script_claims,
        "resource_hashes": resources, "dependency_provenance": "Python standard library only",
        "reproducible_install": "signed agent-skills commit plus pinned Python runtime",
        "python_version": sys.version.split()[0], "unicode_database_version": unicodedata.unidata_version,
        "unicode_normalization": "NFC",
    }


def run_external_builder(source: Snapshot, output_path: Path, source_context: dict[str, object] | None, builder: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path, media_type: str, mode: str) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": "build", "operation_schema_version": OPERATION_VERSION,
        "input_snapshot": str(source.path), "output_directory": str(output_path.parent),
        "output_path": str(output_path), "input_sha256": source.sha256,
        "input_media_type": media_type, "derivation_mode": mode,
    }
    if source_context is not None:
        request["strict_origin"] = {
            "sources": [{"source_id": sid, "snapshot": str(item["snapshot"].path), "asset_id": item["summary"]["asset_id"], "evidence_digest": item["summary"]["evidence_digest"]} for sid, item in source_context["sources"].items()],
            "transformation": source_context["transformation"],
        }
    response = run_tool(builder, request, policy=policy, limits=limits, operation="builder", workdir=workdir)
    if response.get("status") != "built" or response.get("builder_id") != builder["id"]:
        raise OrigenError("INVALID_BUILDER_RESPONSE", "approved builder did not confirm the expected build")
    return response


def external_inspect(snapshot: Snapshot, source_context: dict[str, object] | None, inspector: dict[str, object], *, policy: dict[str, object], limits: dict[str, int | float], workdir: Path, media_type: str, original_snapshot: Snapshot | None = None, c2pa_action: str | None = None) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": "inspect-final", "operation_schema_version": OPERATION_VERSION,
        "snapshot": str(snapshot.path), "sha256": snapshot.sha256, "media_type": media_type,
        "required_coverage": sorted(FINAL_COVERAGE),
    }
    if source_context is not None:
        request["strict_origin"] = {
            "source_summary": source_context["summary"], "operation": source_context.get("transformation"),
        }
    if original_snapshot is not None:
        request["original_provenance"] = {
            "snapshot": str(original_snapshot.path), "sha256": original_snapshot.sha256,
            "requested_action": c2pa_action,
        }
    response = run_tool(inspector, request, policy=policy, limits=limits, operation="inspector", workdir=workdir)
    if response.get("status") != "inspected" or response.get("inspector_id") != inspector["id"]:
        raise OrigenError("INVALID_INSPECTOR_RESPONSE", "approved inspector did not confirm the expected inspection")
    coverage = response.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != FINAL_COVERAGE:
        raise OrigenError("INSPECTOR_COVERAGE_INCOMPLETE", "Final Inspector did not report every required property")
    unknown = [key for key, value in coverage.items() if value == "unknown"]
    failed = [key for key, value in coverage.items() if value not in {"clean", "valid", "not_present", "within_limits", "covered", "consistent", "decodable"}]
    if unknown or failed:
        raise OrigenError("FINAL_INSPECTION_FAILED", "Final Inspector reported unknown or failed coverage", unknown=unknown, failed=failed)
    signals = response.get("content_signals", {"state": "unknown", "checks": []})
    if not isinstance(signals, dict) or signals.get("state") not in CONTENT_SIGNAL_STATES or not isinstance(signals.get("checks"), list):
        raise OrigenError("INVALID_INSPECTOR_RESPONSE", "content_signals result is malformed")
    if signals["state"] == "detected":
        raise OrigenError("CONTENT_SIGNAL_DETECTED", "content-origin signal was detected and Policy rejects publication")
    if source_context is not None and not all(response.get(key) is True for key in ("operation_validated", "source_bindings_validated", "output_validated")):
        raise OrigenError("STRICT_INSPECTION_INCOMPLETE", "independent inspector did not revalidate operation, sources, and output")
    return {"coverage": coverage, "content_signals": signals, "c2pa": response.get("c2pa", {})}


def builtin_final_inspection(snapshot: Snapshot, *, inspection: dict[str, object], c2pa_original: dict[str, object] | None = None) -> dict[str, object]:
    if inspection["findings"] or inspection["c2pa_markers"]:
        raise OrigenError("FINAL_INSPECTION_FAILED", "built-in Final Inspector found prohibited structure", findings=inspection["findings"], c2pa=inspection["c2pa_markers"])
    if inspection["structural_provenance"] != "clean" or inspection["provenance_status"] != "clean":
        raise OrigenError("FINAL_INSPECTION_UNKNOWN", "built-in Final Inspector cannot prove structural coverage")
    coverage = {key: "covered" for key in FINAL_COVERAGE}
    coverage.update({
        "file_type": "valid", "container_validity": "valid", "mime_extension_consistency": "consistent",
        "metadata": "not_present", "c2pa": "not_present", "exif_xmp_iptc": "not_present",
        "active_content": "not_present", "embedded_files": "not_present", "external_references": "not_present",
        "decodability": "decodable", "resource_limits": "within_limits", "policy_coverage": "covered",
    })
    return {
        "coverage": coverage,
        "content_signals": {"state": "unknown", "checks": []},
        "c2pa": c2pa_original or {"original_status": "not_present", "original_manifest_digest": None, "action": "none"},
    }


def fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_directory_noreplace(source: Path, destination: Path) -> None:
    try:
        origen_atomic.rename_directory_noreplace(source, destination)
    except FileExistsError as error:
        raise OrigenError("OUTPUT_EXISTS", "publish bundle already exists", path=str(destination)) from error
    except NotImplementedError as error:
        raise OrigenError("ATOMIC_NOREPLACE_UNAVAILABLE", str(error)) from error


def write_bundle_atomic(bundle: Path, asset: Snapshot, evidence: dict[str, object], receipt: dict[str, object]) -> None:
    bundle.parent.mkdir(parents=True, exist_ok=True)
    if bundle.exists() or bundle.is_symlink():
        raise OrigenError("OUTPUT_EXISTS", "publish bundle already exists", path=str(bundle))
    temporary = Path(tempfile.mkdtemp(prefix=f".{bundle.name}.", dir=bundle.parent))
    try:
        shutil.copyfile(asset.path, temporary / "asset", follow_symlinks=False)
        for name, value in (("evidence.json", evidence), ("receipt.json", receipt)):
            path = temporary / name
            with path.open("wb") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        fsync_path(temporary / "asset")
        fsync_path(temporary)
        rename_directory_noreplace(temporary, bundle)
        fsync_path(bundle.parent)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def write_file_noreplace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise OrigenError("OUTPUT_EXISTS", "refusing to overwrite an existing output", path=str(path)) from error
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_path(path.parent)
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def discover_config_path(raw: str | None) -> Path:
    candidate = raw or os.environ.get("ORIGEN_CONFIG")
    if candidate:
        return Path(candidate)
    return Path.cwd() / ".origen" / "config.json"


def policy_context(args: argparse.Namespace, store: SnapshotStore, core: Any) -> tuple[dict[str, object], str, dict[str, int | float]]:
    config_path = discover_config_path(getattr(args, "config", None))
    config_snapshot = store.capture(config_path, label="Origen config", maximum=4 * 1024 * 1024)
    config = load_config(config_snapshot, core)
    policy, policy_document = merged_policy(config)
    raw_registry = Path(str(config.get("provider_registry", "providers.json")))
    registry_path = raw_registry if raw_registry.is_absolute() else config_path.parent / raw_registry
    registry_snapshot = store.capture(registry_path, label="Provider registry", maximum=4 * 1024 * 1024)
    registry = load_registry(registry_snapshot, core)
    signers = {alias: merge_provider_entry(registry, alias, "signers") for alias in registry["signers"]}
    timestamp_providers = {
        alias: merge_provider_entry(registry, alias, "timestamp_providers")
        for alias in registry["timestamp_providers"]
    }
    builders = {alias: merge_provider_entry(registry, alias, "builders") for alias in registry["builders"]}
    inspectors = {alias: merge_provider_entry(registry, alias, "inspectors") for alias in registry["inspectors"]}
    for field, values in (("approved_signers", signers), ("approved_timestamp_providers", timestamp_providers), ("approved_builders", builders), ("approved_inspectors", inspectors)):
        policy[field] = values
    for alias, category in ((config["root_signer"], signers), (config["final_signer"], signers), (config["timestamp_provider"], timestamp_providers)):
        if alias not in category:
            raise OrigenError("CONFIG_ALIAS_NOT_FOUND", f"configured alias {alias!r} is absent from the Provider registry")
    policy["_root_signer"] = config["root_signer"]
    policy["_final_signer"] = config["final_signer"]
    policy["_timestamp_provider"] = config["timestamp_provider"]
    policy["_policy_document"] = policy_document
    policy["_registry_digest"] = digest_bytes(canonical_bytes(registry))
    executable_dirs = sorted({str(Path(entry["executable"]).parent) for entry in [*signers.values(), *timestamp_providers.values(), *builders.values(), *inspectors.values()] if isinstance(entry.get("executable"), str)})
    environment = dict(policy["environment_policy"])
    environment["approved_path"] = list(dict.fromkeys([*environment.get("approved_path", []), *executable_dirs]))
    policy["environment_policy"] = environment
    setattr(args, "timestamp_provider_id", config["timestamp_provider"])
    return policy, digest_bytes(canonical_bytes(policy_document)), policy_limits(policy)


def command_root(args: argparse.Namespace, core: Any) -> dict[str, object]:
    with SnapshotStore() as store:
        policy, policy_digest, limits = policy_context(args, store, core)
        asset = store.capture(Path(args.asset), label="Human Source", maximum=int(limits["input_file_bytes"]))
        inspection = inspect_snapshot(asset, policy=policy, limits=limits, core=core, publication_profile=args.publication_profile)
        signer = resolve_tool(policy, "approved_signers", policy["_root_signer"], role="root-attestor")
        local_time = timestamp_now()
        root_subject = {
            "creator_id": args.creator_id, "origin_id": args.origin_id,
            "asset_sha256": asset.sha256, "asset_size": asset.size, "media_type": inspection["asset"]["media_type"],
            "policy_digest": policy_digest, "signer_key_id": signer["key_id"],
            "signer_identity": signer["signer_identity"], "signer_provider_id": signer["provider_id"],
            "algorithm": signer["algorithm"], "verifier": signer["verifier"],
            "local_claimed_time": local_time,
        }
        with tempfile.TemporaryDirectory(prefix="root-authorize-", dir=store.root) as work:
            authorization_statement, authorization_proof = authorize_root(root_subject, signer, policy=policy, limits=limits, workdir=Path(work))
        provider = resolve_tool(policy, "approved_timestamp_providers", policy["_timestamp_provider"])
        with tempfile.TemporaryDirectory(prefix="timestamp-", dir=store.root) as work:
            timestamp_statement, timestamp_proof = obtain_trusted_timestamp(
                {"root_subject": root_subject, "authorization": authorization_statement},
                provider, policy=policy, limits=limits, workdir=Path(work),
            )
        timestamp_tool = tool_claim(provider, identity_field="provider_identity")
        assurance_level = "trusted_time"
        record = asset_record(asset, inspection)
        statement: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "operation_schema_version": OPERATION_VERSION,
            "evidence_type": "human-root",
            "created_at": local_time,
            "policy": policy_claim(policy, policy_digest),
            "asset": record,
            "input_asset": None,
            "origin": {"creator_id": args.creator_id, "origin_id": args.origin_id},
            "event": {
                "action": "human-root-attestation", "tool": "origen", "version": VERSION,
            },
            "assurance": {
                "structural": {"state": inspection["structural_provenance"], "coverage": [], "inspector_id": "origen/builtin-root-inspection"},
                "content_signals": {"state": "unknown", "checks": []},
                "derivation": {"mode": "root", "no_unmapped_generated_content": False},
                "root": {"verified": True, "assurance_level": assurance_level},
            },
            "actors": {"creator_id": args.creator_id, "instruction_actor": "human", "content_basis": "human-source-assertion", "builder_actor": None},
            "identities": {"signer": signer_identity(signer)},
            "toolchain": {"signer": tool_claim(signer, identity_field="signer_identity"), "timestamp_provider": timestamp_tool, "provider_registry_digest": policy["_registry_digest"]},
            "timestamp": timestamp_statement,
            "lineage": {"root_asset_id": record["id"], "root_evidence_digest": None, "parent_asset_id": None, "parent_evidence_digest": None},
            "source_mapping": None,
            "publication": {"representation": None, "allowed_transport_metadata": []},
            "authorization": authorization_statement,
            "publish_ready": False,
        }
        with tempfile.TemporaryDirectory(prefix="root-sign-", dir=store.root) as work:
            signature_proof = sign_statement(statement, signer, policy=policy, limits=limits, workdir=Path(work))
        evidence = {**statement, "proof": {**signature_proof, **authorization_proof, **timestamp_proof}}
        output = Path(args.evidence)
        write_file_noreplace(output, json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
        return {
            "status": "root-captured", "schema_version": SCHEMA_VERSION, "asset_id": record["id"],
            "evidence": str(output), "evidence_digest": evidence_digest(evidence),
            "policy_digest": policy_digest, "root_assurance_level": assurance_level, "publish_ready": False,
        }


def command_inspect(args: argparse.Namespace, core: Any) -> dict[str, object]:
    with SnapshotStore() as store:
        policy, _, limits = policy_context(args, store, core)
        snapshot = store.capture(Path(args.asset), label="inspection asset", maximum=int(limits["input_file_bytes"]))
        inspection = inspect_snapshot(snapshot, policy=policy, limits=limits, core=core, publication_profile=args.publication_profile)
        inspection["snapshot_sha256"] = snapshot.sha256
        inspection["publish_ready"] = False
        return inspection


def load_root_arg(args: argparse.Namespace, *, policy: dict[str, object], policy_digest: str, verifier: dict[str, object] | None, store: SnapshotStore, limits: dict[str, int | float], core: Any) -> tuple[dict[str, object] | None, Snapshot | None]:
    if not args.root_evidence:
        if policy.get("root_required", True):
            raise OrigenError("ROOT_REQUIRED", "Trust Policy requires a verified Human Root")
        return None, None
    snapshot = store.capture(Path(args.root_evidence), label="Root evidence", maximum=int(limits["source_map_bytes"]))
    evidence = verify_evidence_snapshot(snapshot, policy=policy, policy_digest=policy_digest, verifier=verifier, timestamp_provider_id=args.timestamp_provider_id, store=store, limits=limits, core=core)
    if evidence["evidence_type"] != "human-root":
        raise OrigenError("INVALID_ROOT", "root evidence must be human-root")
    return evidence, snapshot


def command_strict_compose(args: argparse.Namespace, core: Any) -> dict[str, object]:
    with SnapshotStore() as store:
        policy, policy_digest, limits = policy_context(args, store, core)
        root, _ = load_root_arg(args, policy=policy, policy_digest=policy_digest, verifier=None, store=store, limits=limits, core=core)
        if root is None:
            raise OrigenError("STRICT_ROOT_REQUIRED", "strict-compose requires a verified Human Root")
        map_path = Path(args.source_map)
        map_snapshot = store.capture(map_path, label="source map", maximum=int(limits["source_map_bytes"]))
        context = compose_source_map(map_snapshot, map_original_path=map_path, policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, root_evidence=root, store=store, limits=limits, core=core)
        if context["kind"] != "text" or context["assembled"] is None:
            raise OrigenError("STRICT_COMPOSE_TEXT_ONLY", "strict-compose currently supports TXT and Markdown source maps")
        write_file_noreplace(Path(args.output), context["assembled"])
        return {"status": "strict-composed", "output": str(args.output), "sha256": digest_bytes(context["assembled"]), "source_mapping": context["summary"], "publish_ready": False}


def command_finalize(args: argparse.Namespace, core: Any) -> dict[str, object]:
    with SnapshotStore() as store:
        policy, policy_digest, limits = policy_context(args, store, core)
        signer = resolve_tool(policy, "approved_signers", policy["_final_signer"], role="final-attestor")
        root, _ = load_root_arg(args, policy=policy, policy_digest=policy_digest, verifier=None, store=store, limits=limits, core=core)
        parent: dict[str, object] | None = None
        if args.parent_evidence:
            parent_snapshot = store.capture(Path(args.parent_evidence), label="Parent evidence", maximum=int(limits["source_map_bytes"]))
            parent = verify_evidence_snapshot(parent_snapshot, policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, store=store, limits=limits, core=core)
        source = store.capture(Path(args.asset), label="Finalize input", maximum=int(limits["input_file_bytes"]))
        input_inspection = inspect_snapshot(source, policy=policy, limits=limits, core=core, publication_profile=args.publication_profile)
        media_type = input_inspection["asset"]["media_type"]
        if media_type in UNSUPPORTED_FINAL_MEDIA_TYPES:
            raise OrigenError("UNSUPPORTED_FORMAT", "this format has no Phase 1 finalize contract", media_type=media_type)
        mode = args.guarantee_level
        source_context: dict[str, object] | None = None
        if mode == "strict_origin":
            if root is None or not args.source_map:
                raise OrigenError("SOURCE_MAP_REQUIRED", "STRICT ORIGIN requires Human Root and source map")
            map_path = Path(args.source_map)
            map_snapshot = store.capture(map_path, label="source map", maximum=int(limits["source_map_bytes"]))
            source_context = compose_source_map(map_snapshot, map_original_path=map_path, policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, root_evidence=root, store=store, limits=limits, core=core)
            if source_context["kind"] == "text":
                if media_type not in TEXT_MEDIA_TYPES:
                    raise OrigenError("STRICT_TEXT_TYPE_UNSUPPORTED", "Strict text supports TXT and Markdown")
                try:
                    proposed = core.canonical_text_output(source.read_bytes().decode("utf-8", errors="strict"))
                except (UnicodeDecodeError, core.OrigenError) as error:
                    raise OrigenError("INVALID_UTF8", "Strict final text input is invalid") from error
                if proposed != source_context["assembled"]:
                    raise OrigenError("STRICT_CONTENT_MISMATCH", "final text was not rebuilt solely from signed Human source mapping")
            else:
                primary = source_context["sources"][source_context["primary_source_id"]]
                if source.sha256 != primary["snapshot"].sha256:
                    raise OrigenError("STRICT_CONTENT_MISMATCH", "Strict media input is not the signed primary source snapshot")
        elif args.source_map:
            raise OrigenError("SOURCE_MAP_NOT_ALLOWED", "STANDARD does not accept a Strict source map")

        builder: dict[str, object] | None = None
        inspector: dict[str, object] | None = None
        schema_resource: dict[str, object] | None = None
        external_required = media_type not in BUILTIN_MEDIA_TYPES or bool(input_inspection["c2pa_markers"])
        if source_context and source_context["kind"] == "media" and source_context["transformation"]["op"] != "identity":
            external_required = True
        if external_required:
            builder = resolve_tool(policy, "approved_builders", args.builder_id)
            inspector = resolve_tool(policy, "approved_inspectors", args.inspector_id)
            builder_files = {(item["kind"], item["sha256"]) for item in builder["verified_files"]}
            inspector_files = {(item["kind"], item["sha256"]) for item in inspector["verified_files"]}
            if builder["id"] == inspector["id"] or (builder_files and builder_files == inspector_files):
                raise OrigenError("INSPECTOR_NOT_INDEPENDENT", "builder and Final Inspector must be independently approved identities")
        elif args.builder_id or args.inspector_id:
            if args.builder_id:
                builder = resolve_tool(policy, "approved_builders", args.builder_id)
            if args.inspector_id:
                inspector = resolve_tool(policy, "approved_inspectors", args.inspector_id)

        c2pa_record: dict[str, object] | None = None
        if input_inspection["c2pa_markers"]:
            c2pa_policy = policy.get("c2pa_policy", {})
            action = c2pa_policy.get("action") if isinstance(c2pa_policy, dict) else None
            if action not in {"preserve", "reissue", "detach"} or inspector is None:
                raise OrigenError("C2PA_POLICY_REQUIRED", "embedded/external C2PA requires explicit action and approved external Inspector")
            c2pa_record = {"original_status": "pending-external-validation", "original_manifest_digest": None, "action": action, "markers": input_inspection["c2pa_markers"]}

        with tempfile.TemporaryDirectory(prefix="finalize-", dir=store.root) as work_raw:
            work = Path(work_raw)
            output_dir = work / "output"
            output_dir.mkdir(mode=0o700)
            temporary_output = output_dir / "asset"
            if source_context and source_context["kind"] == "text":
                temporary_output.write_bytes(source_context["assembled"])
                builder_claim = builtin_toolchain(core)
            elif builder is None:
                if media_type == "application/json":
                    schema_resource = validate_json_with_policy(source, args.json_schema_id, policy=policy, store=store, limits=limits, core=core)
                try:
                    adapter = core.builtin_rebuild(source.path, temporary_output, media_type)
                except core.OrigenError as error:
                    raise OrigenError(error.code, error.message, **error.details) from error
                if adapter is None:
                    raise OrigenError("BUILDER_REQUIRED", "format requires an approved external builder")
                builder_claim = builtin_toolchain(core, schema_resource=schema_resource)
            else:
                run_external_builder(source, temporary_output, source_context, builder, policy=policy, limits=limits, workdir=work, media_type=media_type, mode=mode)
                builder_claim = tool_claim(builder, identity_field="builder_identity")
            output_snapshot = store.capture(temporary_output, label="Adapter output", maximum=int(limits["output_file_bytes"]))
            output_snapshot = Snapshot(
                output_snapshot.path, output_snapshot.sha256, output_snapshot.size, source.name_hint
            )
            final_inspection = inspect_snapshot(output_snapshot, policy=policy, limits=limits, core=core, publication_profile=args.publication_profile)
            final_media_type = final_inspection["asset"]["media_type"]
            if final_media_type != media_type:
                raise OrigenError("FINAL_MEDIA_TYPE_MISMATCH", "builder changed media type")
            if inspector is None:
                inspection_result = builtin_final_inspection(output_snapshot, inspection=final_inspection)
                inspector_claim = {"id": "origen/builtin-final-inspector", "identity": f"origen/builtin-{VERSION}", "coverage": sorted(FINAL_COVERAGE)}
            else:
                inspection_result = external_inspect(
                    output_snapshot, source_context, inspector, policy=policy, limits=limits,
                    workdir=work, media_type=media_type,
                    original_snapshot=source if c2pa_record is not None else None,
                    c2pa_action=c2pa_record["action"] if c2pa_record is not None else None,
                )
                inspector_claim = tool_claim(inspector, identity_field="inspector_identity")
            if c2pa_record is not None:
                external_c2pa = inspection_result.get("c2pa")
                if not isinstance(external_c2pa, dict) or external_c2pa.get("original_status") != "valid" or not isinstance(external_c2pa.get("original_manifest_digest"), str):
                    raise OrigenError("C2PA_VALIDATION_REQUIRED", "external Inspector did not validate original C2PA provenance")
                if external_c2pa.get("action") != c2pa_record["action"]:
                    raise OrigenError("C2PA_ACTION_MISMATCH", "Inspector C2PA action differs from Policy")
                c2pa_record = external_c2pa

            final_record = asset_record(output_snapshot, final_inspection)
            input_record = asset_record(source, input_inspection)
            root_record = root["asset"] if root else None
            parent_record = parent["asset"] if parent else None
            representation = args.publication_representation
            handoff = policy["publisher_handoff_policy"]
            if representation not in handoff["publication_representations"]:
                raise OrigenError("PUBLICATION_REPRESENTATION_NOT_ALLOWED", "publication representation is not allowed by Policy")
            local_time = timestamp_now()
            derivation = {
                "mode": mode,
                "no_unmapped_generated_content": mode == "strict_origin",
                "source_map_digest": source_context["summary"]["source_map_digest"] if source_context else None,
                "final_snapshot_digest": output_snapshot.sha256,
                "operation_schema_version": OPERATION_VERSION,
            }
            statement: dict[str, object] = {
                "schema_version": SCHEMA_VERSION, "operation_schema_version": OPERATION_VERSION,
                "evidence_type": "final-asset", "created_at": local_time,
                "policy": policy_claim(policy, policy_digest), "asset": final_record, "input_asset": input_record,
                "origin": None,
                "event": {"action": "trusted-finalization", "source_kind": args.source_kind, "transformations": args.transformation, "tool": "origen", "version": VERSION},
                "assurance": {
                    "structural": {"state": "clean", "coverage": inspection_result["coverage"], "inspector_id": inspector_claim["id"]},
                    "content_signals": {"state": "unknown", "checks": inspection_result["content_signals"]["checks"]},
                    "derivation": derivation,
                    "root": {"verified": root is not None, "assurance_level": root["assurance"]["root"]["assurance_level"] if root else None},
                },
                "actors": {
                    "creator_id": root["origin"]["creator_id"] if root else None,
                    "instruction_actor": source_context["summary"]["instruction_actor"] if source_context else args.instruction_actor,
                    "content_basis": "signed_human_sources" if mode == "strict_origin" else "untrusted_input_allowed",
                    "builder_actor": "trusted_builder",
                },
                "identities": {"signer": signer_identity(signer)},
                "toolchain": {
                    "signer": tool_claim(signer, identity_field="signer_identity"),
                    "builder": builder_claim, "inspector": inspector_claim,
                    "provider_registry_digest": policy["_registry_digest"],
                    "c2pa": c2pa_record,
                    "unicode_normalization": "NFC", "python_version": sys.version.split()[0], "unicode_database_version": unicodedata.unidata_version,
                },
                "timestamp": {"local_claimed_time": local_time, "root_timestamp_receipt_digest": root["timestamp"]["receipt_digest"] if root else None},
                "lineage": {
                    "root_asset_id": root_record["id"] if root_record else None,
                    "root_evidence_digest": evidence_digest(root) if root else None,
                    "parent_asset_id": parent_record["id"] if parent_record else None,
                    "parent_evidence_digest": evidence_digest(parent) if parent else None,
                },
                "source_mapping": source_context["summary"] if source_context else None,
                "publication": {"representation": representation, "allowed_transport_metadata": handoff.get("allowed_transport_metadata", [])},
                "authorization": None,
                "publish_ready": True,
            }
            with tempfile.TemporaryDirectory(prefix="final-sign-", dir=store.root) as sign_work:
                proof = sign_statement(statement, signer, policy=policy, limits=limits, workdir=Path(sign_work))
            evidence = {**statement, "proof": proof}
            receipt = {
                "asset_sha256": output_snapshot.sha256, "evidence_digest": evidence_digest(evidence),
                "policy_digest": policy_digest, "guarantee_mode": mode, "media_type": media_type,
                "publication_representation": representation,
                "allowed_transport_metadata": handoff.get("allowed_transport_metadata", []),
            }
            write_bundle_atomic(Path(args.bundle), output_snapshot, evidence, receipt)
        return {
            "status": "finalized", "bundle": str(args.bundle), "asset_id": final_record["id"],
            "evidence_digest": receipt["evidence_digest"], "policy_digest": policy_digest,
            "guarantee_level": mode, "assurance": statement["assurance"], "publish_ready": True,
        }


def load_bundle(bundle: Path, store: SnapshotStore, limits: dict[str, int | float], core: Any) -> tuple[Snapshot, Snapshot, dict[str, object], dict[str, object]]:
    asset = store.capture_child(bundle, "asset", label="Final asset", maximum=int(limits["output_file_bytes"]))
    evidence_snapshot = store.capture_child(bundle, "evidence.json", label="Final evidence", maximum=int(limits["source_map_bytes"]))
    receipt_snapshot = store.capture_child(bundle, "receipt.json", label="Bundle receipt", maximum=int(limits["source_map_bytes"]))
    evidence = load_evidence(evidence_snapshot, core, limits)
    receipt = load_strict_json_bytes(receipt_snapshot.read_bytes(), core, label="bundle receipt", max_depth=int(limits["json_depth"]))
    expected_fields = {"asset_sha256", "evidence_digest", "policy_digest", "guarantee_mode", "media_type", "publication_representation", "allowed_transport_metadata"}
    if set(receipt) != expected_fields:
        raise OrigenError("INVALID_RECEIPT", "bundle receipt has unknown or missing fields")
    return asset, evidence_snapshot, evidence, receipt


def verify_lineage_arg(path: str | None, *, label: str, expected_digest: object, expected_asset_id: object, policy: dict[str, object], policy_digest: str, verifier: dict[str, object] | None, timestamp_provider_id: str | None, store: SnapshotStore, limits: dict[str, int | float], core: Any) -> dict[str, object] | None:
    if expected_digest is None and expected_asset_id is None:
        if path:
            raise OrigenError("UNEXPECTED_LINEAGE", f"{label} evidence was supplied without a signed link")
        return None
    if not path:
        raise OrigenError("LINEAGE_INCOMPLETE", f"signed {label} evidence is required")
    snapshot = store.capture(Path(path), label=f"{label} evidence", maximum=int(limits["source_map_bytes"]))
    evidence = verify_evidence_snapshot(snapshot, policy=policy, policy_digest=policy_digest, verifier=verifier, timestamp_provider_id=timestamp_provider_id, store=store, limits=limits, core=core)
    if evidence_digest(evidence) != expected_digest or evidence["asset"]["id"] != expected_asset_id:
        raise OrigenError("LINEAGE_MISMATCH", f"{label} evidence does not match signed lineage")
    return evidence


def command_verify(args: argparse.Namespace, core: Any, *, prepublish: bool) -> dict[str, object]:
    with SnapshotStore() as store:
        policy, policy_digest, limits = policy_context(args, store, core)
        if prepublish and policy["mode"] != "production":
            # Development prepublish remains useful for integration tests, but is
            # explicitly labeled and never accepted by a Production Policy.
            status_name = "development-publish-ready"
        else:
            status_name = "publish-ready" if prepublish else "verified"
        asset, _, evidence, receipt = load_bundle(Path(args.bundle), store, limits, core)
        verify_policy_claim(evidence, policy, policy_digest)
        with tempfile.TemporaryDirectory(prefix="bundle-verify-", dir=store.root) as work:
            verify_statement(evidence, resolve_evidence_signer(policy, evidence), policy=policy, limits=limits, workdir=Path(work))
        expected_asset = validate_asset_record(evidence["asset"], "asset")
        if asset.sha256 != expected_asset["sha256"] or asset.size != expected_asset["size"]:
            raise OrigenError("ASSET_MISMATCH", "bundle asset does not match signed Final Evidence")
        if receipt["asset_sha256"] != asset.sha256 or receipt["evidence_digest"] != evidence_digest(evidence) or receipt["policy_digest"] != policy_digest:
            raise OrigenError("RECEIPT_MISMATCH", "bundle receipt does not bind exact asset, evidence, and Policy")
        if receipt["guarantee_mode"] != evidence["assurance"]["derivation"]["mode"] or receipt["media_type"] != expected_asset["media_type"]:
            raise OrigenError("RECEIPT_MISMATCH", "bundle receipt guarantee/media type differs from Evidence")
        if receipt["publication_representation"] != evidence["publication"]["representation"] or receipt["allowed_transport_metadata"] != evidence["publication"]["allowed_transport_metadata"]:
            raise OrigenError("RECEIPT_MISMATCH", "bundle receipt publication contract differs from Evidence")
        lineage = evidence["lineage"]
        root = verify_lineage_arg(args.root_evidence, label="root", expected_digest=lineage.get("root_evidence_digest"), expected_asset_id=lineage.get("root_asset_id"), policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, store=store, limits=limits, core=core)
        parent = verify_lineage_arg(args.parent_evidence, label="parent", expected_digest=lineage.get("parent_evidence_digest"), expected_asset_id=lineage.get("parent_asset_id"), policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, store=store, limits=limits, core=core)
        if policy.get("root_required", True) and root is None:
            raise OrigenError("ROOT_REQUIRED", "Production Policy requires linked Human Root")
        if parent and parent["lineage"].get("root_evidence_digest") and root and parent["lineage"]["root_evidence_digest"] != evidence_digest(root):
            raise OrigenError("LINEAGE_MISMATCH", "parent and supplied Human Root disagree")
        if evidence["assurance"]["derivation"]["mode"] == "strict_origin":
            if not args.source_map or root is None:
                raise OrigenError("SOURCE_MAP_REQUIRED", "STRICT ORIGIN prepublish requires source map and Human Root")
            map_path = Path(args.source_map)
            map_snapshot = store.capture(map_path, label="source map", maximum=int(limits["source_map_bytes"]))
            context = compose_source_map(map_snapshot, map_original_path=map_path, policy=policy, policy_digest=policy_digest, verifier=None, timestamp_provider_id=args.timestamp_provider_id, root_evidence=root, store=store, limits=limits, core=core)
            if canonical_bytes(context["summary"]) != canonical_bytes(evidence["source_mapping"]):
                raise OrigenError("SOURCE_MAP_MISMATCH", "source map summary differs from signed Final Evidence")
            if context["kind"] == "text":
                rebuilt_digest = digest_bytes(context["assembled"])
                if rebuilt_digest != asset.sha256 or evidence["source_mapping"].get("rebuilt_output_sha256") != asset.sha256:
                    raise OrigenError("REBUILT_DIGEST_MISMATCH", "source snapshots rebuild to bytes different from Final asset")
        if prepublish and evidence.get("publish_ready") is not True:
            raise OrigenError("NOT_PUBLISH_READY", "signed Final Evidence is not publish-ready")
        verified_receipt = dict(receipt)
        verified_receipt.update({
            "verified": True, "status": status_name, "bundle": str(args.bundle),
            "verification_policy_mode": policy["mode"], "publisher_must_rehash_upload": True,
            "publisher_must_not_transform": True,
        })
        return verified_receipt


def command_setup(args: argparse.Namespace, core: Any) -> dict[str, object]:
    output = Path(args.config)
    registry_path = Path(args.provider_registry).resolve()
    with SnapshotStore() as store:
        registry_snapshot = store.capture(registry_path, label="Provider registry", maximum=4 * 1024 * 1024)
        registry = load_registry(registry_snapshot, core)
        config: dict[str, object] = {
            "schema_version": CONFIG_VERSION,
            "root_signer": args.root_signer,
            "final_signer": args.final_signer,
            "timestamp_provider": args.timestamp_provider,
            "provider_registry": os.path.relpath(registry_path, output.parent.resolve()),
            "policy": {},
        }
        if args.policy:
            policy_snapshot = store.capture(Path(args.policy), label="Policy overrides", maximum=4 * 1024 * 1024)
            config["policy"] = load_strict_json_bytes(policy_snapshot.read_bytes(), core, label="Policy overrides", max_depth=int(DEFAULT_LIMITS["json_depth"]))
        if args.root_signer == args.final_signer:
            raise OrigenError("ROLE_ALIAS_COLLISION", "root_signer and final_signer must be distinct logical aliases")
        policy, _ = merged_policy(config)
        signers = {alias: merge_provider_entry(registry, alias, "signers") for alias in registry["signers"]}
        timestamps = {alias: merge_provider_entry(registry, alias, "timestamp_providers") for alias in registry["timestamp_providers"]}
        for alias, values in ((args.root_signer, signers), (args.final_signer, signers), (args.timestamp_provider, timestamps)):
            if alias not in values:
                raise OrigenError("CONFIG_ALIAS_NOT_FOUND", f"configured alias {alias!r} is absent from the Provider registry")
        policy["approved_signers"] = signers
        policy["approved_timestamp_providers"] = timestamps
        policy["approved_builders"] = {}
        policy["approved_inspectors"] = {}
        executable_dirs = sorted({str(Path(entry["executable"]).parent) for entry in [*signers.values(), *timestamps.values()]})
        environment = dict(policy["environment_policy"])
        environment["approved_path"] = list(dict.fromkeys([*environment.get("approved_path", []), *executable_dirs]))
        policy["environment_policy"] = environment
        limits = policy_limits(policy)
        root_signer = resolve_tool(policy, "approved_signers", args.root_signer, role="root-attestor")
        final_signer = resolve_tool(policy, "approved_signers", args.final_signer, role="final-attestor")
        timestamp_provider = resolve_tool(policy, "approved_timestamp_providers", args.timestamp_provider)
        checked: list[dict[str, object]] = []
        for entry, required in (
            (root_signer, {"authorize_root", "verify_authorization", "sign", "verify", "get_public_key"}),
            (final_signer, {"sign", "verify", "get_public_key"}),
            (timestamp_provider, {"timestamp", "verify_timestamp"}),
        ):
            with tempfile.TemporaryDirectory(prefix="setup-provider-", dir=store.root) as work:
                health = run_tool(entry, {"operation": "health", "protocol": "origen-provider/1"}, policy=policy, limits=limits, operation="provider-health", workdir=Path(work))
                capabilities = run_tool(entry, {"operation": "capabilities", "protocol": "origen-provider/1"}, policy=policy, limits=limits, operation="provider-capabilities", workdir=Path(work))
            operations = capabilities.get("operations")
            if health.get("healthy") is not True or not isinstance(operations, list) or not required.issubset(set(operations)):
                raise OrigenError("PROVIDER_SELF_TEST_FAILED", "provider health/capabilities do not satisfy the configured role", provider_id=entry["provider_id"])
            checked.append({"provider_id": entry["provider_id"], "healthy": True, "required_operations": sorted(required)})
        for signer in (root_signer, final_signer):
            with tempfile.TemporaryDirectory(prefix="setup-key-", dir=store.root) as work:
                public = run_tool(signer, {
                    "operation": "get_public_key", "protocol": "origen-signer/1",
                    "key_id": signer["key_id"], "algorithm": signer["algorithm"],
                }, policy=policy, limits=limits, operation="get-public-key", workdir=Path(work))
            if public.get("key_id") != signer["key_id"] or public.get("algorithm") != "Ed25519" or public.get("verifier") != signer["verifier"]:
                raise OrigenError("VERIFIER_REFERENCE_MISMATCH", "provider public verifier information differs from the registry")
        payload = canonical_bytes({"schema_version": "origen-self-test/1", "nonce": digest_bytes(os.urandom(32))})
        with tempfile.TemporaryDirectory(prefix="setup-sign-", dir=store.root) as work:
            signed = run_tool(final_signer, {
                "operation": "sign", "protocol": "origen-signer/1", "role": "final-attestor",
                "key_id": final_signer["key_id"], "algorithm": "Ed25519",
                "payload": base64.b64encode(payload).decode("ascii"), "payload_sha256": digest_bytes(payload),
            }, policy=policy, limits=limits, operation="setup-sign", workdir=Path(work))
            verified = run_tool(final_signer, {
                "operation": "verify", "protocol": "origen-signer/1",
                "key_id": final_signer["key_id"], "algorithm": "Ed25519",
                "payload": base64.b64encode(payload).decode("ascii"), "payload_sha256": digest_bytes(payload),
                "signature": signed.get("signature"), "verifier": final_signer["verifier"],
            }, policy=policy, limits=limits, operation="setup-verify", workdir=Path(work))
        expected_sign_response = {
            "provider_id": final_signer["provider_id"], "key_id": final_signer["key_id"],
            "algorithm": "Ed25519", "signer_identity": final_signer["signer_identity"],
        }
        if any(signed.get(key) != value for key, value in expected_sign_response.items()) or not isinstance(signed.get("signature"), str):
            raise OrigenError("PROVIDER_SELF_TEST_FAILED", "final signer returned unexpected key identity during self-test")
        if verified.get("verified") is not True or any(verified.get(key) != value for key, value in expected_sign_response.items()):
            raise OrigenError("PROVIDER_SELF_TEST_FAILED", "final signer sign/verify self-test failed")
        with tempfile.TemporaryDirectory(prefix="setup-time-", dir=store.root) as work:
            timestamp, proof = obtain_trusted_timestamp({"self_test": digest_bytes(payload)}, timestamp_provider, policy=policy, limits=limits, workdir=Path(work))
            fake = {"timestamp": timestamp, "proof": proof}
            verify_trusted_timestamp(fake, timestamp_provider, policy=policy, limits=limits, workdir=Path(work))
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_file_noreplace(output, json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        return {
            "status": "configured", "config": str(output), "algorithm": "Ed25519", "digest": "SHA-256",
            "root_signer": args.root_signer, "final_signer": args.final_signer,
            "timestamp_provider": args.timestamp_provider, "providers_checked": checked, "self_test": "passed",
        }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="origen", description="Provider-neutral Content Origin / Provenance Trust Gate")
    root.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = root.add_subparsers(dest="command", required=True)

    def add_config(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config", help="Origen config; defaults to ORIGEN_CONFIG or .origen/config.json")

    setup = commands.add_parser("setup")
    setup.add_argument("--provider-registry", required=True)
    setup.add_argument("--root-signer", default="default-root")
    setup.add_argument("--final-signer", default="default-final")
    setup.add_argument("--timestamp-provider", default="default")
    setup.add_argument("--policy", help="optional JSON object containing Trust Policy overrides")
    setup.add_argument("--config", default=str(Path.cwd() / ".origen" / "config.json"))
    setup.set_defaults(handler=lambda args, core: command_setup(args, core))

    inspect = commands.add_parser("inspect")
    inspect.add_argument("asset")
    inspect.add_argument("--publication-profile")
    add_config(inspect)
    inspect.set_defaults(handler=lambda args, core: command_inspect(args, core))

    root_command = commands.add_parser("root")
    root_command.add_argument("asset")
    root_command.add_argument("--creator-id", required=True)
    root_command.add_argument("--origin-id", required=True)
    root_command.add_argument("--evidence", required=True)
    root_command.add_argument("--publication-profile")
    add_config(root_command)
    root_command.set_defaults(handler=lambda args, core: command_root(args, core))

    compose = commands.add_parser("strict-compose")
    compose.add_argument("--source-map", required=True)
    compose.add_argument("--root-evidence", required=True)
    compose.add_argument("--output", required=True)
    add_config(compose)
    compose.set_defaults(handler=lambda args, core: command_strict_compose(args, core))

    finalize = commands.add_parser("finalize")
    finalize.add_argument("asset")
    finalize.add_argument("--bundle", required=True)
    finalize.add_argument("--builder-id")
    finalize.add_argument("--inspector-id")
    finalize.add_argument("--root-evidence")
    finalize.add_argument("--parent-evidence")
    finalize.add_argument("--source-map")
    finalize.add_argument("--source-kind", default="ai-output", choices=("ai-output", "external-tool", "human-edit", "captured-original"))
    finalize.add_argument("--guarantee-level", choices=("standard", "strict_origin"), default="standard")
    finalize.add_argument("--transformation", action="append", default=["canonical build"])
    finalize.add_argument("--instruction-actor", choices=("ai", "human", "tool", "mixed"), default="tool")
    finalize.add_argument("--publication-representation", default="canonical-bytes")
    finalize.add_argument("--publication-profile")
    finalize.add_argument("--json-schema-id")
    add_config(finalize)
    finalize.set_defaults(handler=lambda args, core: command_finalize(args, core))

    for name, prepublish in (("verify", False), ("prepublish", True)):
        verify = commands.add_parser(name)
        verify.add_argument("--bundle", required=True)
        verify.add_argument("--root-evidence")
        verify.add_argument("--parent-evidence")
        verify.add_argument("--source-map")
        add_config(verify)
        verify.set_defaults(handler=lambda args, core, mode=prepublish: command_verify(args, core, prepublish=mode))
    return root


def main(argv: list[str], *, core: Any) -> int:
    forbidden = next((flag for flag in argv if flag in FORBIDDEN_COMMAND_FLAGS), None)
    if forbidden is not None:
        payload = {
            "status": "rejected", "publish_ready": False,
            "error": {
                "code": "COMMAND_OVERRIDE_FORBIDDEN",
                "message": "Origen accepts approved IDs only; direct command override is forbidden",
                "flag": forbidden,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    args = parser().parse_args(argv)
    try:
        result = args.handler(args, core)
    except OrigenError as error:
        payload = {
            "status": "rejected", "publish_ready": False,
            "guarantee_level": getattr(args, "guarantee_level", None),
            "assurance": {"structural": {"state": "unknown"}, "content_signals": {"state": "unknown"}},
            "error": {"code": error.code, "message": error.message, **error.details},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except OSError as error:
        payload = {"status": "rejected", "publish_ready": False, "error": {"code": "IO_ERROR", "message": str(error)}}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
