#!/usr/bin/env python3
"""Deterministic built-in format handling for the Origen Policy engine."""

from __future__ import annotations

import binascii
import codecs
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import unicodedata
import zlib


VERSION = "0.3.0"
REQUIRED_ADAPTER_GUARANTEES = {
    "decoded-content",
    "clean-container-rebuild",
    "metadata-policy-applied",
    "provenance-inspected",
    "output-validated",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
    ".zip": "application/zip",
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


def inspect_text_provenance(media_type: str, text: str) -> tuple[list[dict[str, str]], list[str]]:
    findings: list[dict[str, str]] = []
    markers: list[str] = []
    begin = "-----BEGIN C2PA MANIFEST-----" in text
    end = "-----END C2PA MANIFEST-----" in text
    if begin != end:
        raise OrigenError("MALFORMED_C2PA_TEXT_WRAPPER", "C2PA structured text wrapper is incomplete")
    if begin:
        markers.append("TEXT-structured-C2PA")
    if "C2PATXT" in text:
        markers.append("TEXT-C2PATXT")
    if any("\ufe00" <= character <= "\ufe0f" or "\U000e0100" <= character <= "\U000e01ef" for character in text):
        markers.append("TEXT-variation-selector")
    lowered = text.lower()
    if media_type == "text/html":
        if re.search(r"<script\b[^>]*type\s*=\s*['\"]application/c2pa['\"]", lowered):
            markers.append("HTML-inline-C2PA")
        if re.search(r"<link\b[^>]*rel\s*=\s*['\"]c2pa-manifest['\"]", lowered):
            markers.append("HTML-external-C2PA")
    if media_type == "image/svg+xml" and "c2pa:manifest" in lowered:
        markers.append("SVG-C2PA-manifest")
    if markers:
        findings.append({"code": "C2PA_PROVENANCE_DETECTED", "message": "structured or encoded C2PA provenance marker detected"})
    return findings, sorted(set(markers))


def magic_type(prefix: bytes) -> str | None:
    if prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
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


def normalize_json_strings(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [normalize_json_strings(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise OrigenError("INVALID_JSON", "JSON keys collide after NFC normalization", key=normalized_key)
            normalized[normalized_key] = normalize_json_strings(item)
        return normalized
    return value


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
        provenance_findings, provenance_markers = inspect_text_provenance(media_type, text)
        text_findings = inspect_text_characters(text) + inspect_active_text(media_type, text) + provenance_findings
        findings.extend(text_findings)
        if media_type == "application/json":
            normalized, _ = normalize_text(text, reject_hidden=False)
            load_strict_json(normalized)
        structural = "detected" if text_findings else "clean"
        details = {
            "container_valid": True,
            "metadata": [],
            "provenance_markers": provenance_markers,
            "provenance_status": "present" if provenance_markers else "clean",
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
        value = normalize_json_strings(load_strict_json(text))
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


def canonical_text_output(text: str) -> bytes:
    normalized, _ = normalize_text(text)
    return normalized.rstrip("\n").encode("utf-8") + b"\n"


def builtin_rebuild(source: Path, destination: Path, media_type: str) -> dict[str, object] | None:
    if media_type in {"text/plain", "text/markdown", "application/json"}:
        return rebuild_text(source, destination, media_type)
    if media_type == "image/png":
        return rebuild_png(source, destination)
    return None
