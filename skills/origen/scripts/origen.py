#!/usr/bin/env python3
"""Origen: deterministic Content Origin / Provenance boundary.

The CLI intentionally uses only the Python standard library. Production signing
and unsupported media rebuilding are delegated to explicit external providers.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import codecs
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import unicodedata
import zlib


VERSION = "0.2.0"
SCHEMA_VERSION = "origen-evidence/2"
LEGACY_SCHEMA_VERSION = "origen-evidence/1"
SOURCE_MAP_VERSION = "origen-source-map/1"
SOURCE_KINDS = ("ai-output", "external-tool", "human-edit", "captured-original")
GUARANTEE_LEVELS = ("standard", "strict_origin")
KEY_PROTECTION_MODES = {"kms", "hsm", "hardware-backed", "external-service"}
ALLOWED_FINAL_PROVENANCE_MARKERS = {"C2PA/JUMBF", "caBX"}
REQUIRED_ADAPTER_GUARANTEES = {
    "decoded-content",
    "clean-container-rebuild",
    "metadata-policy-applied",
    "provenance-inspected",
    "output-validated",
}
STRICT_ADAPTER_GUARANTEES = {
    "human-origin-inputs-only",
    "deterministic-transformation",
    "content-origin-mapped",
}
ALLOWED_SEPARATORS = {"", " ", "\n", "\n\n", "\t"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class OrigenError(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def asset_id(digest: str) -> str:
    return f"sha256:{digest}"


def require_regular_file(path: Path, label: str = "asset") -> None:
    if not path.exists():
        raise OrigenError("FILE_NOT_FOUND", f"{label} does not exist", path=str(path))
    if path.is_symlink() or not path.is_file():
        raise OrigenError("NOT_REGULAR_FILE", f"{label} must be a regular non-symlink file", path=str(path))


def normalized_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise OrigenError("INVALID_TIMESTAMP", "timestamp must be RFC3339-compatible", value=value) from error
    if parsed.tzinfo is None:
        raise OrigenError("INVALID_TIMESTAMP", "timestamp must include a timezone", value=value)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


EXTENSION_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def family_for(media_type: str) -> str:
    if media_type in {"text/plain", "text/markdown", "application/json", "application/yaml", "text/html"}:
        return "text"
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    if media_type == "application/pdf":
        return "pdf"
    return "unknown"


def inspect_text_characters(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if text.startswith("\ufeff"):
        findings.append({"code": "TEXT_BOM", "message": "leading UTF-8 BOM will be removed by Clean Build"})
    seen: set[str] = set()
    for index, character in enumerate(text):
        category = unicodedata.category(character)
        codepoint = f"U+{ord(character):04X}"
        if category == "Cc" and character not in {"\t", "\n", "\r"} and "TEXT_CONTROL_CHARACTER" not in seen:
            findings.append({
                "code": "TEXT_CONTROL_CHARACTER",
                "message": f"unexpected control character {codepoint} at code-point index {index}",
            })
            seen.add("TEXT_CONTROL_CHARACTER")
        if category == "Cf" and not (index == 0 and character == "\ufeff") and "TEXT_INVISIBLE_CHARACTER" not in seen:
            findings.append({
                "code": "TEXT_INVISIBLE_CHARACTER",
                "message": f"invisible or formatting character {codepoint} at code-point index {index}",
            })
            seen.add("TEXT_INVISIBLE_CHARACTER")
    return findings


def normalize_text(text: str, *, reject_hidden: bool = True) -> tuple[str, list[dict[str, str]]]:
    findings = inspect_text_characters(text)
    if text.startswith("\ufeff"):
        text = text[1:]
    if reject_hidden:
        blocking = [item for item in findings if item["code"] != "TEXT_BOM"]
        if blocking:
            raise OrigenError(blocking[0]["code"], blocking[0]["message"])
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text), findings


def inspect_active_text(media_type: str, text: str) -> list[dict[str, str]]:
    if media_type not in {"text/html", "image/svg+xml"}:
        return []
    lowered = text.lower()
    patterns = {
        "ACTIVE_SCRIPT": ("<script", "javascript:"),
        "ACTIVE_EMBED": ("<iframe", "<object", "<embed", "<foreignobject"),
        "ACTIVE_REFRESH": ("http-equiv=\"refresh\"", "http-equiv='refresh'"),
    }
    findings = []
    for code, needles in patterns.items():
        if any(needle in lowered for needle in needles):
            findings.append({"code": code, "message": "active HTML/SVG content requires sanitization"})
    if re.search(r"\son[a-z][a-z0-9_-]*\s*=", lowered):
        findings.append({"code": "ACTIVE_EVENT_HANDLER", "message": "active HTML/SVG event handler requires sanitization"})
    return findings


def magic_type(prefix: bytes) -> str | None:
    if prefix.startswith(PNG_SIGNATURE):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        return "audio/wav"
    if prefix.startswith(b"OggS"):
        return "audio/ogg"
    if prefix.startswith(b"ID3") or (len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        brand = prefix[8:12]
        if brand in {b"M4A ", b"M4B ", b"M4P "}:
            return "audio/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        return "video/mp4"
    return None


def detect_media_type(path: Path, *, name_hint: str | None = None) -> tuple[str, list[dict[str, str]]]:
    prefix_limit = 65536
    with path.open("rb") as stream:
        prefix = stream.read(prefix_limit)
        truncated = len(prefix) == prefix_limit and stream.read(1) != b""
    detected = magic_type(prefix)
    suffix = Path(name_hint or path.name).suffix.lower()
    declared = EXTENSION_TYPES.get(suffix)
    findings: list[dict[str, str]] = []

    decoded_as_text = False
    if detected is None and b"\x00" not in prefix:
        try:
            codecs.getincrementaldecoder("utf-8")().decode(prefix, final=not truncated)
        except UnicodeDecodeError:
            pass
        else:
            decoded_as_text = True
            detected = declared if declared in {
                "text/plain", "text/markdown", "application/json", "application/yaml", "text/html", "image/svg+xml"
            } else "text/plain"

    media_type = detected or declared or "application/octet-stream"
    if detected and declared and detected != declared:
        findings.append({
            "code": "MEDIA_TYPE_MISMATCH",
            "message": f"magic bytes identify {detected}, extension identifies {declared}",
        })
    if detected is None and declared and not decoded_as_text:
        findings.append({
            "code": "MEDIA_TYPE_UNCONFIRMED",
            "message": f"extension suggests {declared}, but the binary signature is not recognized",
        })
    return media_type, findings


def parse_png(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise OrigenError("INVALID_PNG", "PNG signature is missing")
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    seen_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise OrigenError("INVALID_PNG", "truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        if not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in chunk_type):
            raise OrigenError("INVALID_PNG", "invalid PNG chunk type")
        end = offset + 12 + length
        if end > len(data):
            raise OrigenError("INVALID_PNG", "truncated PNG chunk data", chunk=chunk_type.decode("ascii"))
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise OrigenError("INVALID_PNG", "PNG chunk CRC mismatch", chunk=chunk_type.decode("ascii"))
        chunks.append((chunk_type, chunk_data))
        offset = end
        if chunk_type == b"IEND":
            seen_iend = True
            break
    if not seen_iend or offset != len(data):
        raise OrigenError("INVALID_PNG", "PNG must end exactly at IEND")
    if not chunks or chunks[0][0] != b"IHDR" or sum(kind == b"IHDR" for kind, _ in chunks) != 1:
        raise OrigenError("INVALID_PNG", "PNG must contain exactly one leading IHDR")
    if sum(kind == b"IEND" for kind, _ in chunks) != 1:
        raise OrigenError("INVALID_PNG", "PNG must contain exactly one IEND")
    return chunks


def inspect_png(path: Path) -> dict[str, object]:
    chunks = parse_png(path.read_bytes())
    names = [kind.decode("ascii") for kind, _ in chunks]
    metadata_names = [
        name for name in names
        if name in {"tEXt", "zTXt", "iTXt", "eXIf", "tIME", "caBX", "iCCP", "pHYs"}
    ]
    provenance = [name for name in names if name == "caBX"]
    for kind, chunk_data in chunks:
        if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            lowered = chunk_data.lower()
            if b"c2pa" in lowered or b"content credential" in lowered or b"xmp" in lowered:
                provenance.append(kind.decode("ascii"))
    structural = "detected" if metadata_names or provenance else "clean"
    return {
        "container_valid": True,
        "chunks": names,
        "metadata": sorted(set(metadata_names)),
        "provenance_markers": sorted(set(provenance)),
        "provenance_status": "present" if provenance else "clean",
        "structural_provenance": structural,
    }


def inspect_jpeg(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise OrigenError("INVALID_JPEG", "JPEG SOI/EOI markers are missing")
    markers: list[str] = []
    provenance: list[str] = []
    lowered = data.lower()
    if b"c2pa" in lowered or b"content credentials" in lowered or b"jumb" in lowered:
        provenance.append("C2PA/JUMBF")
    if b"http://ns.adobe.com/xap/1.0/" in lowered:
        provenance.append("XMP")
    if b"exif\x00\x00" in lowered:
        markers.append("EXIF")
    if b"photoshop 3.0" in lowered:
        markers.append("IPTC")
    structural = "detected" if markers or provenance else "unknown"
    return {
        "container_valid": True,
        "metadata": sorted(set(markers)),
        "provenance_markers": sorted(set(provenance)),
        "provenance_status": "present" if provenance else "unknown",
        "structural_provenance": structural,
    }


def inspect_pdf(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
        raise OrigenError("INVALID_PDF", "PDF header or terminal EOF marker is missing")
    lowered = data.lower()
    markers = []
    if b"/metadata" in lowered or b"<?xpacket" in lowered:
        markers.append("XMP")
    if b"c2pa" in lowered or b"jumb" in lowered:
        markers.append("C2PA/JUMBF")
    return {
        "container_valid": True,
        "metadata": markers,
        "provenance_markers": markers,
        "provenance_status": "present" if markers else "unknown",
        "structural_provenance": "detected" if markers else "unknown",
    }


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise OrigenError("INVALID_JSON", "JSON contains duplicate object keys", key=key)
        value[key] = item
    return value


def reject_nonfinite(value: str) -> object:
    raise OrigenError("INVALID_JSON", "JSON contains NaN or Infinity", value=value)


def load_strict_json(data: str) -> object:
    try:
        return json.loads(
            data,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except OrigenError:
        raise
    except json.JSONDecodeError as error:
        raise OrigenError("INVALID_JSON", "JSON cannot be parsed", line=error.lineno, column=error.colno) from error


def inspect_asset(path: Path, *, name_hint: str | None = None) -> dict[str, object]:
    require_regular_file(path)
    media_type, findings = detect_media_type(path, name_hint=name_hint)
    digest, size = hash_file(path)
    details: dict[str, object]
    if media_type == "image/png":
        details = inspect_png(path)
    elif media_type == "image/jpeg":
        details = inspect_jpeg(path)
    elif media_type == "application/pdf":
        details = inspect_pdf(path)
    elif media_type in {"text/plain", "text/markdown", "application/json", "text/html", "image/svg+xml"}:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise OrigenError("INVALID_UTF8", "text asset is not strict UTF-8") from error
        text_findings = inspect_text_characters(text) + inspect_active_text(media_type, text)
        findings.extend(text_findings)
        if media_type == "application/json":
            normalized, _ = normalize_text(text, reject_hidden=False)
            load_strict_json(normalized)
        structural = "detected" if text_findings else "clean"
        details = {
            "container_valid": True,
            "metadata": [],
            "provenance_markers": [],
            "provenance_status": "clean",
            "structural_provenance": structural,
        }
    else:
        details = {
            "container_valid": media_type != "application/octet-stream",
            "metadata": [],
            "provenance_markers": [],
            "provenance_status": "unknown",
            "structural_provenance": "unknown",
        }
    if any(item["code"] == "MEDIA_TYPE_MISMATCH" for item in findings):
        details["provenance_status"] = "unknown"
        details["structural_provenance"] = "unknown"
    return {
        "status": "inspected",
        "asset": {
            "id": asset_id(digest),
            "sha256": digest,
            "size": size,
            "media_type": media_type,
            "family": family_for(media_type),
        },
        "findings": findings,
        **details,
        "content_provenance": "unknown",
        "publish_ready": False,
    }


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def validate_png_header(header: bytes) -> tuple[int, int, int, int]:
    if len(header) != 13:
        raise OrigenError("INVALID_PNG", "IHDR must be 13 bytes")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    if width == 0 or height == 0:
        raise OrigenError("INVALID_PNG", "PNG dimensions must be positive")
    allowed = {
        0: ({1, 2, 4, 8, 16}, 1),
        2: ({8, 16}, 3),
        3: ({1, 2, 4, 8}, 1),
        4: ({8, 16}, 2),
        6: ({8, 16}, 4),
    }
    if color_type not in allowed or bit_depth not in allowed[color_type][0]:
        raise OrigenError("UNSUPPORTED_PNG", "unsupported PNG color type or bit depth")
    if compression != 0 or filtering != 0 or interlace != 0:
        raise OrigenError("UNSUPPORTED_PNG", "only standard non-interlaced PNG is supported")
    return width, height, bit_depth, allowed[color_type][1]


def decompress_png_idat(compressed: bytes, expected_size: int) -> bytes:
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected_size + 1)
        raw += decoder.flush()
    except zlib.error as error:
        raise OrigenError("INVALID_PNG", "PNG IDAT zlib stream is invalid") from error
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise OrigenError("INVALID_PNG", "PNG IDAT contains trailing or incomplete compressed data")
    if len(raw) != expected_size:
        raise OrigenError("INVALID_PNG", "PNG scanline length does not match IHDR")
    return raw


def rebuild_png(source: Path, destination: Path) -> dict[str, object]:
    chunks = parse_png(source.read_bytes())
    by_name: dict[bytes, list[bytes]] = {}
    for kind, data in chunks:
        by_name.setdefault(kind, []).append(data)
        if kind[0] & 0x20 == 0 and kind not in {b"IHDR", b"PLTE", b"IDAT", b"IEND"}:
            raise OrigenError("UNSUPPORTED_PNG", "unknown critical PNG chunk", chunk=kind.decode("ascii"))
    if any(kind in by_name for kind in (b"acTL", b"fcTL", b"fdAT")):
        raise OrigenError("UNSUPPORTED_PNG", "animated PNG requires a trusted external adapter")
    if b"caBX" in by_name or any(
        b"c2pa" in data.lower() or b"content credential" in data.lower()
        for kind, values in by_name.items() if kind in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"} for data in values
    ):
        raise OrigenError(
            "PROVENANCE_REQUIRES_POLICY",
            "embedded provenance requires a C2PA-aware trusted adapter; it will not be silently removed",
        )
    if b"iCCP" in by_name:
        raise OrigenError("UNSUPPORTED_PNG", "ICC-profile PNG requires a color-managed trusted adapter")
    if len(by_name.get(b"IDAT", [])) == 0:
        raise OrigenError("INVALID_PNG", "PNG has no IDAT data")
    if len(by_name.get(b"PLTE", [])) > 1:
        raise OrigenError("INVALID_PNG", "PNG has multiple PLTE chunks")

    header = by_name[b"IHDR"][0]
    width, height, bit_depth, channels = validate_png_header(header)
    color_type = header[9]
    if color_type == 3 and b"PLTE" not in by_name:
        raise OrigenError("INVALID_PNG", "indexed PNG requires PLTE")
    row_bytes = math.ceil(width * channels * bit_depth / 8)
    expected_size = height * (row_bytes + 1)
    raw = decompress_png_idat(b"".join(by_name[b"IDAT"]), expected_size)
    for row in range(height):
        filter_type = raw[row * (row_bytes + 1)]
        if filter_type > 4:
            raise OrigenError("INVALID_PNG", "PNG contains invalid scanline filter", row=row)

    retained: list[tuple[bytes, bytes]] = [(b"IHDR", header)]
    for kind, expected_length in ((b"cHRM", 32), (b"gAMA", 4), (b"sRGB", 1)):
        values = by_name.get(kind, [])
        if len(values) > 1 or (values and len(values[0]) != expected_length):
            raise OrigenError("INVALID_PNG", "invalid PNG display chunk", chunk=kind.decode("ascii"))
        if values:
            if kind == b"sRGB" and values[0][0] > 3:
                raise OrigenError("INVALID_PNG", "invalid sRGB rendering intent")
            retained.append((kind, values[0]))
    if b"PLTE" in by_name:
        retained.append((b"PLTE", by_name[b"PLTE"][0]))
    if len(by_name.get(b"tRNS", [])) > 1:
        raise OrigenError("INVALID_PNG", "PNG has multiple tRNS chunks")
    if b"tRNS" in by_name:
        retained.append((b"tRNS", by_name[b"tRNS"][0]))
    retained.append((b"IDAT", zlib.compress(raw, level=9)))
    retained.append((b"IEND", b""))
    destination.write_bytes(PNG_SIGNATURE + b"".join(png_chunk(kind, data) for kind, data in retained))
    removed = sorted({kind.decode("ascii") for kind, _ in chunks} - {kind.decode("ascii") for kind, _ in retained})
    return {
        "tool": "origen/builtin-png-container-rebuild",
        "version": VERSION,
        "media_type": "image/png",
        "guarantees": sorted(REQUIRED_ADAPTER_GUARANTEES),
        "removed_chunks": removed,
    }


def rebuild_text(source: Path, destination: Path, media_type: str) -> dict[str, object]:
    try:
        text = source.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OrigenError("INVALID_UTF8", "text asset is not strict UTF-8") from error
    text, sanitization_findings = normalize_text(text)
    if media_type == "application/json":
        value = load_strict_json(text)
        output = canonical_bytes(value) + b"\n"
        tool = "origen/builtin-json-rebuild"
    else:
        output = text.rstrip("\n").encode("utf-8") + b"\n"
        tool = "origen/builtin-utf8-text-rebuild"
    destination.write_bytes(output)
    return {
        "tool": tool,
        "version": VERSION,
        "media_type": media_type,
        "guarantees": sorted(REQUIRED_ADAPTER_GUARANTEES),
        "sanitization": [item["code"] for item in sanitization_findings],
    }


def resolve_command(command: str, operation: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise OrigenError("INVALID_PROVIDER_COMMAND", f"{operation} command cannot be parsed") from error
    if not argv:
        raise OrigenError("INVALID_PROVIDER_COMMAND", f"{operation} command is empty")
    return argv


def command_toolchain(command: str, response: dict[str, object]) -> dict[str, object]:
    argv = resolve_command(command, "adapter")
    executable_name = shutil.which(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
    if not executable_name:
        raise OrigenError("PROVIDER_NOT_FOUND", "adapter executable was not found", executable=argv[0])
    executable = Path(executable_name).resolve()
    require_regular_file(executable, "adapter executable")
    executable_digest, _ = hash_file(executable)
    command_files = []
    for argument in argv[1:]:
        candidate = Path(argument)
        if candidate.exists():
            candidate = candidate.resolve()
            require_regular_file(candidate, "adapter command file")
            digest, _ = hash_file(candidate)
            command_files.append({"path": str(candidate), "sha256": digest})
    dependency_provenance = response.get("dependency_provenance")
    reproducible_install = response.get("reproducible_install")
    if not isinstance(dependency_provenance, str) or not dependency_provenance:
        raise OrigenError("INVALID_ADAPTER_RESPONSE", "trusted adapter must report dependency_provenance")
    if not isinstance(reproducible_install, str) or not reproducible_install:
        raise OrigenError("INVALID_ADAPTER_RESPONSE", "trusted adapter must report reproducible_install")
    return {
        "tool": response["tool"],
        "version": response["version"],
        "executable_path": str(executable),
        "executable_sha256": executable_digest,
        "command_files": command_files,
        "dependency_provenance": dependency_provenance,
        "reproducible_install": reproducible_install,
    }


def builtin_toolchain(adapter: dict[str, object]) -> dict[str, object]:
    runtime = Path(sys.executable).resolve()
    runtime_digest, _ = hash_file(runtime)
    script = Path(__file__).resolve()
    script_digest, _ = hash_file(script)
    return {
        "tool": adapter["tool"],
        "version": adapter["version"],
        "executable_path": str(runtime),
        "runtime_version": platform.python_version(),
        "executable_sha256": runtime_digest,
        "origen_script_sha256": script_digest,
        "hash_implementation": "Python hashlib SHA-256",
        "dependency_provenance": "Python standard library only",
        "reproducible_install": f"Python {sys.version_info.major}.{sys.version_info.minor} plus the signed Origen script revision",
    }


def run_json_command(command: str, request: dict[str, object], *, operation: str) -> dict[str, object]:
    argv = resolve_command(command, operation)
    try:
        completed = subprocess.run(
            argv,
            input=canonical_bytes(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )
    except FileNotFoundError as error:
        raise OrigenError("PROVIDER_NOT_FOUND", f"{operation} provider executable was not found", executable=argv[0]) from error
    except subprocess.TimeoutExpired as error:
        raise OrigenError("PROVIDER_TIMEOUT", f"{operation} provider timed out") from error
    if completed.returncode != 0:
        raise OrigenError(
            "PROVIDER_FAILED",
            f"{operation} provider returned non-zero",
            returncode=completed.returncode,
        )
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} provider did not return one JSON object") from error
    if not isinstance(response, dict):
        raise OrigenError("INVALID_PROVIDER_RESPONSE", f"{operation} provider response must be an object")
    return response


def sign_evidence(statement: dict[str, object], command: str) -> dict[str, object]:
    payload = canonical_bytes(statement)
    response = run_json_command(
        command,
        {
            "operation": "sign",
            "request_scope": "sign-canonical-evidence-only",
            "payload": base64.b64encode(payload).decode("ascii"),
            "payload_sha256": sha256_bytes(payload),
        },
        operation="sign",
    )
    proof: dict[str, object] = {}
    for key in ("provider", "key_id", "algorithm", "signature"):
        value = response.get(key)
        if not isinstance(value, str) or not value:
            raise OrigenError("INVALID_PROVIDER_RESPONSE", f"sign provider response is missing {key}")
        proof[key] = value
    for key in ("provider_version", "key_protection", "dependency_provenance", "reproducible_install"):
        value = response.get(key)
        if not isinstance(value, str) or not value:
            raise OrigenError("INVALID_PROVIDER_RESPONSE", f"sign provider response is missing {key}")
        proof[key] = value
    if proof["key_protection"] not in KEY_PROTECTION_MODES:
        raise OrigenError("UNSAFE_SIGNING_PROVIDER", "signing provider did not attest an isolated key protection mode")
    if response.get("request_scope") != "sign-canonical-evidence-only":
        raise OrigenError("INVALID_PROVIDER_RESPONSE", "sign provider did not confirm the restricted request scope")
    proof["request_scope"] = response["request_scope"]
    proof["toolchain"] = command_toolchain(command, {
        "tool": response["provider"],
        "version": response["provider_version"],
        "dependency_provenance": response["dependency_provenance"],
        "reproducible_install": response["reproducible_install"],
    })
    return proof


def evidence_statement(evidence: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in evidence.items() if key != "proof"}


def verify_signature(evidence: dict[str, object], command: str) -> None:
    proof = evidence.get("proof")
    if not isinstance(proof, dict):
        raise OrigenError("INVALID_EVIDENCE", "evidence proof is missing")
    payload = canonical_bytes(evidence_statement(evidence))
    response = run_json_command(
        command,
        {
            "operation": "verify",
            "payload": base64.b64encode(payload).decode("ascii"),
            "payload_sha256": sha256_bytes(payload),
            "proof": proof,
        },
        operation="verify",
    )
    if response.get("verified") is not True:
        raise OrigenError("SIGNATURE_INVALID", "evidence signature was not verified")
    identity_keys = ["provider", "key_id", "algorithm"]
    if evidence.get("schema_version") == SCHEMA_VERSION:
        identity_keys.extend(("provider_version", "key_protection"))
    for key in identity_keys:
        if response.get(key) != proof.get(key):
            raise OrigenError("SIGNATURE_IDENTITY_MISMATCH", f"verify provider returned a different {key}")


def write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def load_evidence(path: Path) -> dict[str, object]:
    require_regular_file(path, "evidence")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OrigenError("INVALID_EVIDENCE", "evidence is not valid UTF-8 JSON", path=str(path)) from error
    if not isinstance(value, dict):
        raise OrigenError("INVALID_EVIDENCE", "evidence root must be an object")
    validate_evidence_shape(value)
    return value


def require_string(mapping: dict[str, object], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise OrigenError("INVALID_EVIDENCE", f"{context}.{key} must be a non-empty string")
    return value


def validate_asset_record(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OrigenError("INVALID_EVIDENCE", f"{context} must be an object")
    digest = require_string(value, "sha256", context)
    identifier = require_string(value, "id", context)
    require_string(value, "media_type", context)
    if not SHA256_RE.fullmatch(digest) or identifier != asset_id(digest):
        raise OrigenError("INVALID_EVIDENCE", f"{context} hash identity is invalid")
    if not isinstance(value.get("size"), int) or value["size"] < 0:
        raise OrigenError("INVALID_EVIDENCE", f"{context}.size must be a non-negative integer")
    return value


def validate_evidence_shape(value: dict[str, object]) -> None:
    schema_version = value.get("schema_version")
    if schema_version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
        raise OrigenError("UNSUPPORTED_EVIDENCE_SCHEMA", "unsupported Origen evidence schema")
    evidence_type = value.get("evidence_type")
    if evidence_type not in {"human-root", "final-asset"}:
        raise OrigenError("INVALID_EVIDENCE", "evidence_type is invalid")
    normalized_timestamp(require_string(value, "created_at", "evidence"))
    validate_asset_record(value.get("asset"), "asset")
    proof = value.get("proof")
    if not isinstance(proof, dict):
        raise OrigenError("INVALID_EVIDENCE", "proof must be an object")
    for key in ("provider", "key_id", "algorithm", "signature"):
        require_string(proof, key, "proof")
    if schema_version == SCHEMA_VERSION:
        for key in ("provider_version", "key_protection", "dependency_provenance", "reproducible_install"):
            require_string(proof, key, "proof")
        if proof.get("key_protection") not in KEY_PROTECTION_MODES:
            raise OrigenError("INVALID_EVIDENCE", "v2 proof uses an untrusted key protection mode")
        if proof.get("request_scope") != "sign-canonical-evidence-only" or not isinstance(proof.get("toolchain"), dict):
            raise OrigenError("INVALID_EVIDENCE", "v2 proof is missing restricted scope or signing toolchain")
    if not isinstance(value.get("publish_ready"), bool):
        raise OrigenError("INVALID_EVIDENCE", "publish_ready must be boolean")
    if evidence_type == "human-root":
        if value["publish_ready"] is not False:
            raise OrigenError("INVALID_EVIDENCE", "human-root cannot be publish-ready")
        origin = value.get("origin")
        if not isinstance(origin, dict):
            raise OrigenError("INVALID_EVIDENCE", "human-root origin is missing")
        require_string(origin, "creator_id", "origin")
        require_string(origin, "origin_id", "origin")
    else:
        if value["publish_ready"] is not True:
            raise OrigenError("INVALID_EVIDENCE", "final-asset evidence must be publish-ready")
        validate_asset_record(value.get("input_asset"), "input_asset")
        event = value.get("event")
        if not isinstance(event, dict) or event.get("action") != "trusted-finalization" or event.get("source_kind") not in SOURCE_KINDS:
            raise OrigenError("INVALID_EVIDENCE", "final event source_kind is invalid")
        transformations = event.get("transformations")
        if not isinstance(transformations, list) or not transformations or any(not isinstance(item, str) or not item for item in transformations):
            raise OrigenError("INVALID_EVIDENCE", "final transformations must be a non-empty string list")
        adapter = event.get("adapter")
        if not isinstance(adapter, dict):
            raise OrigenError("INVALID_EVIDENCE", "final adapter record is missing")
        require_string(adapter, "tool", "event.adapter")
        require_string(adapter, "version", "event.adapter")
        guarantees = adapter.get("guarantees")
        if (
            not isinstance(guarantees, list)
            or any(not isinstance(item, str) for item in guarantees)
            or not REQUIRED_ADAPTER_GUARANTEES.issubset(set(guarantees))
        ):
            raise OrigenError("INVALID_EVIDENCE", "final adapter guarantees are incomplete")
        if adapter.get("embedded_provenance") not in {"none", "validated-final"}:
            raise OrigenError("INVALID_EVIDENCE", "final embedded provenance policy is invalid")
        inspection = value.get("inspection")
        if not isinstance(inspection, dict) or inspection.get("provenance_status") not in {"clean", "verified-by-adapter", "validated-final"}:
            raise OrigenError("INVALID_EVIDENCE", "final provenance inspection was not conclusively verified")
        lineage = value.get("lineage")
        if not isinstance(lineage, dict):
            raise OrigenError("INVALID_EVIDENCE", "final lineage is missing")
        for label in ("root", "parent"):
            linked_asset_id = lineage.get(f"{label}_asset_id")
            linked_digest = lineage.get(f"{label}_evidence_digest")
            if (linked_asset_id is None) != (linked_digest is None):
                raise OrigenError("INVALID_EVIDENCE", f"{label} lineage fields must both be null or both be set")
            if linked_asset_id is not None:
                if not isinstance(linked_asset_id, str) or not linked_asset_id.startswith("sha256:"):
                    raise OrigenError("INVALID_EVIDENCE", f"{label} asset id is invalid")
                if not isinstance(linked_digest, str) or not SHA256_RE.fullmatch(linked_digest):
                    raise OrigenError("INVALID_EVIDENCE", f"{label} evidence digest is invalid")
        if schema_version == SCHEMA_VERSION:
            guarantee = value.get("guarantee")
            if not isinstance(guarantee, dict):
                raise OrigenError("INVALID_EVIDENCE", "v2 final evidence requires a guarantee decision")
            level = guarantee.get("level")
            structural = guarantee.get("structural_provenance")
            content = guarantee.get("content_provenance")
            root_verified = guarantee.get("root_verified")
            if level not in GUARANTEE_LEVELS or structural != "clean" or not isinstance(root_verified, bool):
                raise OrigenError("INVALID_EVIDENCE", "final guarantee decision is invalid")
            if level == "standard" and content != "unknown":
                raise OrigenError("INVALID_EVIDENCE", "STANDARD must not overclaim content-level provenance")
            if level == "strict_origin" and (content != "verified_clean" or root_verified is not True):
                raise OrigenError("INVALID_EVIDENCE", "STRICT ORIGIN requires verified Human content and root")
            toolchain = value.get("toolchain")
            if not isinstance(toolchain, dict):
                raise OrigenError("INVALID_EVIDENCE", "v2 final evidence requires a toolchain record")
            for key in ("tool", "version", "dependency_provenance", "reproducible_install"):
                require_string(toolchain, key, "toolchain")
            if not SHA256_RE.fullmatch(require_string(toolchain, "executable_sha256", "toolchain")):
                raise OrigenError("INVALID_EVIDENCE", "toolchain executable hash is invalid")
            if level == "strict_origin":
                if not isinstance(value.get("source_mapping"), dict):
                    raise OrigenError("INVALID_EVIDENCE", "STRICT ORIGIN requires a signed source mapping")
            elif value.get("source_mapping") is not None:
                raise OrigenError("INVALID_EVIDENCE", "STANDARD evidence must not contain a Strict source mapping")


def evidence_digest(evidence: dict[str, object]) -> str:
    return sha256_bytes(canonical_bytes(evidence))


def verify_asset_record(path: Path, expected: dict[str, object], *, name_hint: str | None = None) -> None:
    inspection = inspect_asset(path, name_hint=name_hint)
    actual = inspection["asset"]
    for key in ("id", "sha256", "size", "media_type"):
        if actual[key] != expected[key]:
            raise OrigenError("ASSET_MISMATCH", f"asset {key} does not match signed evidence")


def canonical_text_output(text: str) -> bytes:
    normalized, _ = normalize_text(text)
    return normalized.rstrip("\n").encode("utf-8") + b"\n"


def load_verified_source_map(
    path: Path,
    *,
    verify_command: str,
    root_evidence: dict[str, object],
) -> dict[str, object]:
    require_regular_file(path, "source map")
    try:
        raw = load_strict_json(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise OrigenError("INVALID_SOURCE_MAP", "source map must be strict UTF-8 JSON") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != SOURCE_MAP_VERSION:
        raise OrigenError("INVALID_SOURCE_MAP", "unsupported source map schema")
    kind = raw.get("kind")
    if kind not in {"text", "media"}:
        raise OrigenError("INVALID_SOURCE_MAP", "source map kind must be text or media")
    source_values = raw.get("sources")
    if not isinstance(source_values, list) or not source_values:
        raise OrigenError("SOURCE_MAP_INCOMPLETE", "source map requires at least one signed Human source")

    sources: dict[str, dict[str, object]] = {}
    source_summaries = []
    base = path.parent
    for item in source_values:
        if not isinstance(item, dict):
            raise OrigenError("INVALID_SOURCE_MAP", "each source map source must be an object")
        source_id = item.get("source_id")
        asset_value = item.get("asset")
        evidence_value = item.get("evidence")
        if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id) or source_id in sources:
            raise OrigenError("INVALID_SOURCE_MAP", "source_id must be unique and portable")
        if not isinstance(asset_value, str) or not isinstance(evidence_value, str):
            raise OrigenError("INVALID_SOURCE_MAP", "source asset and evidence paths must be strings")
        source_candidate = base / asset_value if not Path(asset_value).is_absolute() else Path(asset_value)
        evidence_candidate = base / evidence_value if not Path(evidence_value).is_absolute() else Path(evidence_value)
        require_regular_file(source_candidate, "source asset")
        require_regular_file(evidence_candidate, "source evidence")
        source_path = source_candidate.resolve()
        evidence_path = evidence_candidate.resolve()
        evidence = load_evidence(evidence_path)
        if evidence.get("evidence_type") != "human-root":
            raise OrigenError("STRICT_SOURCE_NOT_HUMAN", "Strict Origin sources must use human-root evidence", source_id=source_id)
        verify_signature(evidence, verify_command)
        expected = validate_asset_record(evidence.get("asset"), f"source[{source_id}].asset")
        verify_asset_record(source_path, expected)
        summary = {
            "source_id": source_id,
            "asset_id": expected["id"],
            "evidence_digest": evidence_digest(evidence),
        }
        sources[source_id] = {
            "asset_path": source_path,
            "evidence_path": evidence_path,
            "evidence": evidence,
            "summary": summary,
        }
        source_summaries.append(summary)

    root_digest = evidence_digest(root_evidence)
    root_asset_id = validate_asset_record(root_evidence.get("asset"), "root.asset")["id"]
    if not any(
        item["summary"]["evidence_digest"] == root_digest and item["summary"]["asset_id"] == root_asset_id
        for item in sources.values()
    ):
        raise OrigenError("SOURCE_MAP_ROOT_MISSING", "source map must include the supplied primary Human Root")

    summary: dict[str, object] = {
        "schema_version": SOURCE_MAP_VERSION,
        "kind": kind,
        "sources": source_summaries,
    }
    assembled: bytes | None = None
    if kind == "text":
        operations = raw.get("operations")
        if not isinstance(operations, list) or not operations:
            raise OrigenError("SOURCE_MAP_INCOMPLETE", "text source map requires operations")
        normalized_sources: dict[str, str] = {}
        for source_id, item in sources.items():
            media_type = item["evidence"]["asset"]["media_type"]
            if media_type not in {"text/plain", "text/markdown"}:
                raise OrigenError("STRICT_TEXT_SOURCE_UNSUPPORTED", "Strict text sources must be plain text or Markdown")
            try:
                text = item["asset_path"].read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise OrigenError("INVALID_UTF8", "Strict text source is not UTF-8", source_id=source_id) from error
            normalized_sources[source_id], _ = normalize_text(text)
        pieces = []
        normalized_operations = []
        for operation in operations:
            if not isinstance(operation, dict):
                raise OrigenError("INVALID_SOURCE_MAP", "source map operation must be an object")
            op = operation.get("op")
            if op == "slice":
                source_id = operation.get("source_id")
                start = operation.get("start")
                end = operation.get("end")
                if source_id not in normalized_sources or not isinstance(start, int) or not isinstance(end, int):
                    raise OrigenError("INVALID_SOURCE_MAP", "slice requires a known source_id and integer bounds")
                source_text = normalized_sources[source_id]
                if start < 0 or end < start or end > len(source_text):
                    raise OrigenError("SOURCE_MAP_RANGE_INVALID", "slice bounds are outside the normalized Human source")
                pieces.append(source_text[start:end])
                normalized_operations.append({"op": "slice", "source_id": source_id, "start": start, "end": end})
            elif op == "separator":
                value = operation.get("value")
                if value not in ALLOWED_SEPARATORS:
                    raise OrigenError("STRICT_LITERAL_FORBIDDEN", "Strict text permits only fixed whitespace separators")
                pieces.append(value)
                normalized_operations.append({"op": "separator", "value": value})
            else:
                raise OrigenError("INVALID_SOURCE_MAP", "unsupported source map operation", operation=op)
        assembled = canonical_text_output("".join(pieces))
        summary["operations"] = normalized_operations
    else:
        primary_source_id = raw.get("primary_source_id")
        transformation = raw.get("transformation")
        if primary_source_id not in sources:
            raise OrigenError("SOURCE_MAP_INCOMPLETE", "media source map requires a known primary_source_id")
        if not isinstance(transformation, dict) or transformation.get("op") not in {"identity", "trusted-deterministic"}:
            raise OrigenError("INVALID_SOURCE_MAP", "media transformation must be identity or trusted-deterministic")
        canonical_bytes(transformation)
        summary["primary_source_id"] = primary_source_id
        summary["transformation"] = transformation

    summary["mapping_digest"] = sha256_bytes(canonical_bytes(summary))
    return {
        "kind": kind,
        "sources": sources,
        "summary": summary,
        "assembled": assembled,
        "primary_source_id": raw.get("primary_source_id"),
        "transformation": raw.get("transformation"),
    }


def verify_signed_source_map(
    path: Path | None,
    *,
    expected: object,
    verify_command: str,
    root_evidence: dict[str, object] | None,
) -> None:
    if path is None or root_evidence is None:
        raise OrigenError("SOURCE_MAP_REQUIRED", "Strict Origin verification requires the source map and Human Root")
    actual = load_verified_source_map(path, verify_command=verify_command, root_evidence=root_evidence)
    if not isinstance(expected, dict) or canonical_bytes(actual["summary"]) != canonical_bytes(expected):
        raise OrigenError("SOURCE_MAP_MISMATCH", "source map does not match signed final evidence")


def verify_linked_evidence(
    *,
    label: str,
    path: Path | None,
    expected_digest: object,
    expected_asset_id: object,
    verify_command: str,
) -> dict[str, object] | None:
    if expected_digest is None and expected_asset_id is None:
        if path is not None:
            raise OrigenError("UNEXPECTED_LINEAGE", f"{label} evidence was supplied but no link is signed")
        return None
    if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest):
        raise OrigenError("INVALID_EVIDENCE", f"{label} evidence digest is invalid")
    if not isinstance(expected_asset_id, str) or not expected_asset_id.startswith("sha256:"):
        raise OrigenError("INVALID_EVIDENCE", f"{label} asset id is invalid")
    if path is None:
        raise OrigenError("LINEAGE_INCOMPLETE", f"signed {label} evidence is required to verify the chain")
    linked = load_evidence(path)
    verify_signature(linked, verify_command)
    if evidence_digest(linked) != expected_digest:
        raise OrigenError("LINEAGE_MISMATCH", f"{label} evidence digest does not match")
    linked_asset = validate_asset_record(linked.get("asset"), f"{label}.asset")
    if linked_asset["id"] != expected_asset_id:
        raise OrigenError("LINEAGE_MISMATCH", f"{label} asset id does not match")
    return linked


def make_asset_record(path: Path, *, name_hint: str | None = None) -> dict[str, object]:
    inspection = inspect_asset(path, name_hint=name_hint)
    asset = dict(inspection["asset"])
    asset.pop("family", None)
    return asset


def command_inspect(args: argparse.Namespace) -> dict[str, object]:
    return inspect_asset(Path(args.asset))


def command_root(args: argparse.Namespace) -> dict[str, object]:
    asset = Path(args.asset).resolve()
    evidence_path = Path(args.evidence).resolve()
    require_regular_file(asset)
    if not args.creator_id.strip() or not args.origin_id.strip():
        raise OrigenError("IDENTITY_REQUIRED", "creator-id and origin-id must be non-empty")
    if evidence_path.exists():
        raise OrigenError("OUTPUT_EXISTS", "evidence output already exists", path=str(evidence_path))
    inspection = inspect_asset(asset)
    record = dict(inspection["asset"])
    record.pop("family", None)
    statement: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": "human-root",
        "created_at": normalized_timestamp(args.timestamp),
        "asset": record,
        "origin": {"creator_id": args.creator_id, "origin_id": args.origin_id},
        "event": {"action": "human-root-captured", "tool": "origen", "version": VERSION},
        "inspection": {
            "container_valid": inspection["container_valid"],
            "provenance_status": inspection["provenance_status"],
            "structural_provenance": inspection["structural_provenance"],
            "content_provenance": "unknown",
            "findings": inspection["findings"],
        },
        "publish_ready": False,
    }
    evidence = {**statement, "proof": sign_evidence(statement, args.sign_command)}
    write_json_atomic(evidence_path, evidence)
    return {
        "status": "root-captured",
        "asset_id": record["id"],
        "evidence": str(evidence_path),
        "evidence_digest": evidence_digest(evidence),
        "publish_ready": False,
    }


def builtin_rebuild(source: Path, destination: Path, media_type: str) -> dict[str, object] | None:
    if media_type in {"text/plain", "text/markdown", "application/json"}:
        return rebuild_text(source, destination, media_type)
    if media_type == "image/png":
        return rebuild_png(source, destination)
    return None


def external_rebuild(
    source: Path,
    destination: Path,
    inspection: dict[str, object],
    command: str,
    *,
    guarantee_level: str,
    source_context: dict[str, object] | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": "rebuild",
        "input_path": str(source),
        "output_path": str(destination),
        "input_media_type": inspection["asset"]["media_type"],
        "input_family": inspection["asset"]["family"],
        "guarantee_level": guarantee_level,
    }
    if source_context is not None:
        request["strict_origin"] = {
            "verified_sources": [
                {
                    "source_id": source_id,
                    "asset_path": str(item["asset_path"]),
                    "asset_id": item["summary"]["asset_id"],
                    "evidence_digest": item["summary"]["evidence_digest"],
                }
                for source_id, item in source_context["sources"].items()
            ],
            "transformation": source_context["transformation"],
        }
    response = run_json_command(
        command,
        request,
        operation="adapter",
    )
    if response.get("status") != "rebuilt":
        raise OrigenError("ADAPTER_REJECTED", "trusted adapter did not report rebuilt status")
    for key in ("tool", "version", "media_type"):
        if not isinstance(response.get(key), str) or not response[key]:
            raise OrigenError("INVALID_ADAPTER_RESPONSE", f"trusted adapter response is missing {key}")
    guarantees = response.get("guarantees")
    if not isinstance(guarantees, list) or any(not isinstance(item, str) for item in guarantees):
        raise OrigenError("INVALID_ADAPTER_RESPONSE", "trusted adapter guarantees must be a string list")
    missing = sorted(REQUIRED_ADAPTER_GUARANTEES - set(guarantees))
    if guarantee_level == "strict_origin":
        missing.extend(sorted(STRICT_ADAPTER_GUARANTEES - set(guarantees)))
    if missing:
        raise OrigenError("ADAPTER_GUARANTEE_MISSING", "trusted adapter omitted required guarantees", missing=missing)
    require_regular_file(destination, "adapter output")
    response["toolchain"] = command_toolchain(command, response)
    return response


def command_finalize(args: argparse.Namespace) -> dict[str, object]:
    source = Path(args.asset).resolve()
    output = Path(args.output).resolve()
    evidence_path = Path(args.evidence).resolve()
    require_regular_file(source)
    if output == source or evidence_path in {source, output}:
        raise OrigenError("INVALID_OUTPUT", "input, output, and evidence paths must be distinct")
    for path in (output, evidence_path):
        if path.exists():
            raise OrigenError("OUTPUT_EXISTS", "refusing to overwrite an existing output", path=str(path))
    if not args.transformation or any(not item.strip() for item in args.transformation):
        raise OrigenError("TRANSFORMATION_REQUIRED", "at least one non-empty transformation is required")

    guarantee_level = args.guarantee_level
    if guarantee_level == "standard" and args.source_map:
        raise OrigenError("SOURCE_MAP_NOT_ALLOWED", "source mapping is reserved for Strict Origin")
    input_inspection = inspect_asset(source)
    ambiguous_findings = [item for item in input_inspection["findings"] if item["code"].startswith("MEDIA_TYPE_")]
    if ambiguous_findings:
        raise OrigenError("INPUT_AMBIGUOUS", "input media type is ambiguous", findings=ambiguous_findings)
    if input_inspection["asset"]["family"] == "unknown":
        raise OrigenError("UNSUPPORTED_FORMAT", "unknown binary input cannot be finalized")

    root: dict[str, object] | None = None
    parent: dict[str, object] | None = None
    if args.root_evidence:
        if not args.verify_command:
            raise OrigenError("VERIFY_PROVIDER_REQUIRED", "root evidence requires a verification provider")
        root = load_evidence(Path(args.root_evidence).resolve())
        if root.get("evidence_type") != "human-root":
            raise OrigenError("INVALID_ROOT", "root evidence must be human-root")
        verify_signature(root, args.verify_command)
    if args.parent_evidence:
        if not args.verify_command:
            raise OrigenError("VERIFY_PROVIDER_REQUIRED", "parent evidence requires a verification provider")
        parent = load_evidence(Path(args.parent_evidence).resolve())
        verify_signature(parent, args.verify_command)
        parent_lineage = parent.get("lineage") if parent.get("evidence_type") == "final-asset" else None
        if isinstance(parent_lineage, dict) and parent_lineage.get("root_evidence_digest") is not None:
            if root is None:
                raise OrigenError("LINEAGE_INCOMPLETE", "parent has a Human Root; root evidence is also required")
            if parent_lineage.get("root_evidence_digest") != evidence_digest(root):
                raise OrigenError("LINEAGE_MISMATCH", "parent and supplied root evidence disagree")
            if parent_lineage.get("root_asset_id") != root["asset"]["id"]:
                raise OrigenError("LINEAGE_MISMATCH", "parent and supplied root asset disagree")

    source_context: dict[str, object] | None = None
    if guarantee_level == "strict_origin":
        if root is None or not args.verify_command:
            raise OrigenError("STRICT_ROOT_REQUIRED", "Strict Origin requires a verified signed Human Root")
        if args.source_kind not in {"human-edit", "captured-original"}:
            raise OrigenError("STRICT_AI_CONTENT_FORBIDDEN", "Strict Origin cannot accept AI/external generated final content")
        if not args.source_map:
            raise OrigenError("SOURCE_MAP_REQUIRED", "Strict Origin requires a complete source map")
        source_context = load_verified_source_map(
            Path(args.source_map).resolve(),
            verify_command=args.verify_command,
            root_evidence=root,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    # Stage in the output directory so the final os.replace stays on one filesystem.
    with tempfile.TemporaryDirectory(prefix=".origen-finalize-", dir=str(output.parent)) as temporary_dir:
        temporary_output = Path(temporary_dir) / ("final" + output.suffix.lower())
        media_type = input_inspection["asset"]["media_type"]
        if guarantee_level == "strict_origin" and source_context is not None:
            if source_context["kind"] == "text":
                if media_type not in {"text/plain", "text/markdown"}:
                    raise OrigenError("STRICT_TEXT_TYPE_UNSUPPORTED", "Strict text finalization supports plain text and Markdown")
                try:
                    proposed_text = source.read_text(encoding="utf-8", errors="strict")
                except UnicodeDecodeError as error:
                    raise OrigenError("INVALID_UTF8", "Strict final text must be UTF-8") from error
                if canonical_text_output(proposed_text) != source_context["assembled"]:
                    raise OrigenError(
                        "STRICT_CONTENT_MISMATCH",
                        "final text contains content not produced by the signed Human source mapping",
                    )
                temporary_output.write_bytes(source_context["assembled"])
                adapter = {
                    "tool": "origen/builtin-strict-text-compose",
                    "version": VERSION,
                    "media_type": media_type,
                    "guarantees": sorted(REQUIRED_ADAPTER_GUARANTEES | STRICT_ADAPTER_GUARANTEES),
                    "embedded_provenance": "none",
                }
            else:
                primary = source_context["sources"][source_context["primary_source_id"]]
                if input_inspection["asset"]["id"] != primary["summary"]["asset_id"]:
                    raise OrigenError(
                        "STRICT_CONTENT_MISMATCH",
                        "Strict media input must be the signed Human primary source, not generated pixel/waveform/frame data",
                    )
                transformation_op = source_context["transformation"]["op"]
                if transformation_op == "identity":
                    adapter = builtin_rebuild(source, temporary_output, media_type)
                    if adapter is None:
                        if not args.adapter_command:
                            raise OrigenError(
                                "TRUSTED_ADAPTER_REQUIRED",
                                "Strict identity rebuild has no built-in adapter for this media type",
                                media_type=media_type,
                            )
                        adapter = external_rebuild(
                            source,
                            temporary_output,
                            input_inspection,
                            args.adapter_command,
                            guarantee_level=guarantee_level,
                            source_context=source_context,
                        )
                    else:
                        adapter["guarantees"] = sorted(set(adapter["guarantees"]) | STRICT_ADAPTER_GUARANTEES)
                else:
                    if not args.adapter_command:
                        raise OrigenError("TRUSTED_ADAPTER_REQUIRED", "Strict deterministic media transformation requires an adapter")
                    adapter = external_rebuild(
                        source,
                        temporary_output,
                        input_inspection,
                        args.adapter_command,
                        guarantee_level=guarantee_level,
                        source_context=source_context,
                    )
        else:
            adapter = builtin_rebuild(source, temporary_output, media_type)
            if adapter is None:
                if not args.adapter_command:
                    raise OrigenError(
                        "TRUSTED_ADAPTER_REQUIRED",
                        "format has no built-in Clean Build adapter",
                        media_type=media_type,
                    )
                adapter = external_rebuild(
                    source,
                    temporary_output,
                    input_inspection,
                    args.adapter_command,
                    guarantee_level=guarantee_level,
                )

        if "toolchain" not in adapter:
            adapter["toolchain"] = builtin_toolchain(adapter)

        final_inspection = inspect_asset(temporary_output, name_hint=output.name)
        if final_inspection["findings"]:
            raise OrigenError("FINAL_VALIDATION_FAILED", "final asset retains prohibited or ambiguous structure", findings=final_inspection["findings"])
        if final_inspection["asset"]["family"] != input_inspection["asset"]["family"]:
            raise OrigenError("FINAL_FAMILY_MISMATCH", "trusted rebuild changed the media family")
        if final_inspection["asset"]["media_type"] != adapter["media_type"]:
            raise OrigenError("FINAL_MEDIA_TYPE_MISMATCH", "adapter media type does not match final bytes")
        provenance_status = final_inspection["provenance_status"]
        structural_provenance = final_inspection["structural_provenance"]
        markers = final_inspection["provenance_markers"]
        metadata = final_inspection["metadata"]
        embedded_policy = adapter.get("embedded_provenance", "none")
        if embedded_policy not in {"none", "validated-final"}:
            raise OrigenError("INVALID_ADAPTER_RESPONSE", "trusted adapter returned an invalid embedded provenance policy")
        if markers and embedded_policy != "validated-final":
            raise OrigenError("FINAL_PROVENANCE_PRESENT", "final asset contains embedded provenance not validated for this final")
        if markers and embedded_policy == "validated-final":
            unexpected_markers = sorted(set(markers) - ALLOWED_FINAL_PROVENANCE_MARKERS)
            unexpected_metadata = sorted(set(metadata) - {"C2PA/JUMBF", "caBX"})
            if unexpected_markers or unexpected_metadata:
                raise OrigenError(
                    "FINAL_STRUCTURAL_PROVENANCE_DETECTED",
                    "validated-final policy cannot authorize unrelated metadata or provenance",
                    markers=unexpected_markers,
                    metadata=unexpected_metadata,
                )
            provenance_status = "validated-final"
            structural_provenance = "clean"
        if structural_provenance == "detected":
            raise OrigenError("FINAL_STRUCTURAL_PROVENANCE_DETECTED", "prohibited structural provenance remains after rebuild")
        if structural_provenance == "unknown":
            if args.adapter_command and "provenance-inspected" in adapter["guarantees"]:
                provenance_status = "verified-by-adapter"
                structural_provenance = "clean"
            else:
                raise OrigenError("FINAL_STRUCTURAL_PROVENANCE_UNKNOWN", "final structural provenance state is unknown")
        if structural_provenance != "clean":
            raise OrigenError("FINAL_STRUCTURAL_PROVENANCE_UNKNOWN", "final structural provenance is not clean")
        reported_content = adapter.get("content_provenance", "unknown")
        if reported_content == "detected":
            raise OrigenError("CONTENT_PROVENANCE_DETECTED", "trusted adapter detected content-level provenance")
        if reported_content not in {"unknown", "verified_clean"}:
            raise OrigenError("INVALID_ADAPTER_RESPONSE", "invalid content_provenance status")
        content_provenance = "verified_clean" if guarantee_level == "strict_origin" else "unknown"

        final_asset = dict(final_inspection["asset"])
        final_asset.pop("family", None)
        input_asset = dict(input_inspection["asset"])
        input_asset.pop("family", None)
        root_asset = validate_asset_record(root["asset"], "root.asset") if root else None
        parent_asset = validate_asset_record(parent["asset"], "parent.asset") if parent else None
        statement: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "evidence_type": "final-asset",
            "created_at": normalized_timestamp(args.timestamp),
            "asset": final_asset,
            "input_asset": input_asset,
            "event": {
                "action": "trusted-finalization",
                "source_kind": args.source_kind,
                "transformations": [item.strip() for item in args.transformation],
                "adapter": {
                    "tool": adapter["tool"],
                    "version": adapter["version"],
                    "guarantees": sorted(set(adapter["guarantees"])),
                    "embedded_provenance": embedded_policy,
                },
                "tool": "origen",
                "version": VERSION,
            },
            "inspection": {
                "container_valid": final_inspection["container_valid"],
                "provenance_status": provenance_status,
                "structural_provenance": structural_provenance,
                "content_provenance": content_provenance,
                "provenance_markers": markers,
                "metadata": metadata,
            },
            "lineage": {
                "root_asset_id": root_asset["id"] if root_asset else None,
                "root_evidence_digest": evidence_digest(root) if root else None,
                "parent_asset_id": parent_asset["id"] if parent_asset else None,
                "parent_evidence_digest": evidence_digest(parent) if parent else None,
            },
            "guarantee": {
                "level": guarantee_level,
                "structural_provenance": structural_provenance,
                "content_provenance": content_provenance,
                "root_verified": root is not None,
            },
            "toolchain": adapter["toolchain"],
            "publish_ready": True,
        }
        if source_context is not None:
            statement["source_mapping"] = source_context["summary"]
        evidence = {**statement, "proof": sign_evidence(statement, args.sign_command)}

        os.replace(temporary_output, output)
        try:
            write_json_atomic(evidence_path, evidence)
        except Exception:
            if output.exists():
                output.unlink()
            raise

    return {
        "status": "finalized",
        "asset": str(output),
        "asset_id": final_asset["id"],
        "evidence": str(evidence_path),
        "evidence_digest": evidence_digest(evidence),
        "guarantee_level": guarantee_level,
        "structural_provenance": structural_provenance,
        "content_provenance": content_provenance,
        "root_verified": root is not None,
        "publish_ready": True,
    }


def verify_common(args: argparse.Namespace, *, prepublish: bool) -> dict[str, object]:
    asset = Path(args.asset).resolve()
    evidence = load_evidence(Path(args.evidence).resolve())
    verify_signature(evidence, args.verify_command)
    expected_asset = validate_asset_record(evidence.get("asset"), "asset")
    verify_asset_record(asset, expected_asset)

    chain_verified = True
    root: dict[str, object] | None = None
    if evidence.get("evidence_type") == "final-asset":
        lineage = evidence["lineage"]
        root = verify_linked_evidence(
            label="root",
            path=Path(args.root_evidence).resolve() if args.root_evidence else None,
            expected_digest=lineage.get("root_evidence_digest"),
            expected_asset_id=lineage.get("root_asset_id"),
            verify_command=args.verify_command,
        )
        if root is not None and root.get("evidence_type") != "human-root":
            raise OrigenError("INVALID_ROOT", "linked root evidence must be human-root")
        parent = verify_linked_evidence(
            label="parent",
            path=Path(args.parent_evidence).resolve() if args.parent_evidence else None,
            expected_digest=lineage.get("parent_evidence_digest"),
            expected_asset_id=lineage.get("parent_asset_id"),
            verify_command=args.verify_command,
        )
        if parent is not None and parent.get("evidence_type") == "final-asset":
            parent_lineage = parent["lineage"]
            if parent_lineage.get("root_evidence_digest") is not None:
                if root is None:
                    raise OrigenError("LINEAGE_INCOMPLETE", "linked parent has a Human Root; root evidence is required")
                if parent_lineage.get("root_evidence_digest") != evidence_digest(root):
                    raise OrigenError("LINEAGE_MISMATCH", "linked parent and root evidence disagree")
                if parent_lineage.get("root_asset_id") != root["asset"]["id"]:
                    raise OrigenError("LINEAGE_MISMATCH", "linked parent and root asset disagree")
        if evidence.get("schema_version") == SCHEMA_VERSION:
            guarantee = evidence["guarantee"]
            if guarantee["root_verified"] != (root is not None):
                raise OrigenError("ROOT_VERIFICATION_MISMATCH", "signed root verification status does not match supplied chain")
            if guarantee["level"] == "strict_origin":
                verify_signed_source_map(
                    Path(args.source_map).resolve() if args.source_map else None,
                    expected=evidence.get("source_mapping"),
                    verify_command=args.verify_command,
                    root_evidence=root,
                )
            elif args.source_map:
                raise OrigenError("UNEXPECTED_SOURCE_MAP", "STANDARD evidence does not accept a Strict source map")
    elif args.root_evidence or args.parent_evidence:
        raise OrigenError("UNEXPECTED_LINEAGE", "human-root verification does not accept linked evidence")

    if prepublish:
        if evidence.get("evidence_type") != "final-asset" or evidence.get("publish_ready") is not True:
            raise OrigenError("NOT_PUBLISH_READY", "prepublish requires signed final-asset evidence")
        if evidence.get("schema_version") != SCHEMA_VERSION:
            raise OrigenError("EVIDENCE_UPGRADE_REQUIRED", "legacy final evidence cannot satisfy the v2 guarantee gate")
    if evidence.get("evidence_type") == "final-asset" and evidence.get("schema_version") == SCHEMA_VERSION:
        guarantee = evidence["guarantee"]
        guarantee_level = guarantee["level"]
        structural_provenance = guarantee["structural_provenance"]
        content_provenance = guarantee["content_provenance"]
        root_verified = guarantee["root_verified"]
    elif evidence.get("evidence_type") == "human-root":
        guarantee_level = None
        structural_provenance = evidence.get("inspection", {}).get("structural_provenance", "unknown")
        content_provenance = "unknown"
        root_verified = True
    else:
        guarantee_level = "legacy_standard"
        structural_provenance = "unknown"
        content_provenance = "unknown"
        root_verified = root is not None
    return {
        "status": "publish-ready" if prepublish else "verified",
        "asset_id": expected_asset["id"],
        "evidence_type": evidence["evidence_type"],
        "evidence_digest": evidence_digest(evidence),
        "chain_verified": chain_verified,
        "guarantee_level": guarantee_level,
        "structural_provenance": structural_provenance,
        "content_provenance": content_provenance,
        "root_verified": root_verified,
        "publish_ready": bool(evidence.get("publish_ready")),
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="origen", description="Content Origin / Provenance boundary")
    root.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = root.add_subparsers(dest="command", required=True)

    inspect_command = commands.add_parser("inspect", help="inspect an asset without granting publish permission")
    inspect_command.add_argument("asset")
    inspect_command.set_defaults(handler=command_inspect)

    root_command = commands.add_parser("root", help="capture and externally sign a Human Root")
    root_command.add_argument("asset")
    root_command.add_argument("--creator-id", required=True)
    root_command.add_argument("--origin-id", required=True)
    root_command.add_argument("--sign-command", required=True)
    root_command.add_argument("--evidence", required=True)
    root_command.add_argument("--timestamp")
    root_command.set_defaults(handler=command_root)

    finalize = commands.add_parser("finalize", help="Clean Build an untrusted asset and create signed final evidence")
    finalize.add_argument("asset")
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--evidence", required=True)
    finalize.add_argument("--source-kind", required=True, choices=SOURCE_KINDS)
    finalize.add_argument("--guarantee-level", choices=GUARANTEE_LEVELS, default="standard")
    finalize.add_argument("--source-map")
    finalize.add_argument("--transformation", action="append", required=True)
    finalize.add_argument("--root-evidence")
    finalize.add_argument("--parent-evidence")
    finalize.add_argument("--sign-command", required=True)
    finalize.add_argument("--verify-command")
    finalize.add_argument("--adapter-command")
    finalize.add_argument("--timestamp")
    finalize.set_defaults(handler=command_finalize)

    for name, help_text, prepublish in (
        ("verify", "verify an asset, signature, and signed lineage", False),
        ("prepublish", "fail-closed Publisher gate for a publish-ready final asset", True),
    ):
        verify = commands.add_parser(name, help=help_text)
        verify.add_argument("asset")
        verify.add_argument("--evidence", required=True)
        verify.add_argument("--root-evidence")
        verify.add_argument("--parent-evidence")
        verify.add_argument("--source-map")
        verify.add_argument("--verify-command", required=True)
        verify.set_defaults(handler=lambda args, mode=prepublish: verify_common(args, prepublish=mode))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
    except OrigenError as error:
        payload: dict[str, object] = {
            "status": "rejected",
            "publish_ready": False,
            "guarantee_level": getattr(args, "guarantee_level", None),
            "structural_provenance": "unknown",
            "content_provenance": "unknown",
            "error": {"code": error.code, "message": error.message, **error.details},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except OSError as error:
        payload = {
            "status": "rejected",
            "publish_ready": False,
            "guarantee_level": getattr(args, "guarantee_level", None),
            "structural_provenance": "unknown",
            "content_provenance": "unknown",
            "error": {"code": "IO_ERROR", "message": str(error)},
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
