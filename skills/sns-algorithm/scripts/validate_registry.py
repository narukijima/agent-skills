#!/usr/bin/env python3
"""Validate sns-algorithm source provenance and platform-reference links."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


PLATFORMS = {"x", "youtube", "facebook", "instagram", "threads", "tiktok"}
EVIDENCE_CLASSES = {
    "confirmed_code",
    "confirmed_official",
    "official_guidance",
    "inference",
    "hypothesis",
    "unknown",
    "stale",
}
CONFIDENCE = {"high", "medium", "low"}
SOURCE_TYPES = {
    "official_source_code",
    "official_source_code_documentation",
    "official_historical_source_code",
    "official_system_card",
    "official_system_card_announcement",
    "official_engineering_blog",
    "official_engineering_product_blog",
    "official_help",
    "official_help_creator_documentation",
    "official_creator_guidance",
    "official_product_blog",
    "official_policy",
    "official_safety_transparency",
}
OFFICIAL_HOSTS = {
    "github.com",
    "help.x.com",
    "ai.meta.com",
    "engineering.fb.com",
    "about.fb.com",
    "support.google.com",
    "blog.youtube",
    "support.tiktok.com",
    "newsroom.tiktok.com",
    "www.tiktok.com",
}
SHA_RE = re.compile(r"[0-9a-f]{40}")
ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_date(value: object, field: str, errors: list[str], *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        errors.append(f"{field} must be YYYY-MM-DD" + (" or null" if nullable else ""))
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{field} is not a calendar date")


def _check_text_fields(item: dict, fields: tuple[str, ...], prefix: str, errors: list[str]) -> None:
    for field in fields:
        if not _is_text(item.get(field)):
            errors.append(f"{prefix}.{field} must be a non-empty string")


def validate_registry(payload: dict, skill_root: Path) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    _check_date(payload.get("registry_updated"), "registry_updated", errors, nullable=False)
    if set(payload.get("platforms", [])) != PLATFORMS:
        errors.append("platforms must contain exactly the six supported platforms")
    if set(payload.get("evidence_classes", [])) != EVIDENCE_CLASSES:
        errors.append("evidence_classes do not match the Skill contract")

    sources = payload.get("sources")
    claims = payload.get("claims")
    if not isinstance(sources, list) or not sources:
        return errors + ["sources must be a non-empty list"]
    if not isinstance(claims, list) or not claims:
        return errors + ["claims must be a non-empty list"]

    source_by_id: dict[str, dict] = {}
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _check_text_fields(source, ("id", "title", "url", "source_type", "scope", "limitations"), prefix, errors)
        source_id = source.get("id")
        if isinstance(source_id, str):
            if not ID_RE.fullmatch(source_id):
                errors.append(f"{prefix}.id must use lowercase hyphen-case")
            elif source_id in source_by_id:
                errors.append(f"duplicate source id: {source_id}")
            else:
                source_by_id[source_id] = source
        if source.get("source_type") not in SOURCE_TYPES:
            errors.append(f"{prefix}.source_type is not allowlisted")
        url = source.get("url")
        if isinstance(url, str):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
                errors.append(f"{prefix}.url must be an allowlisted official HTTPS source")
        source_platforms = source.get("platforms")
        if (
            not isinstance(source_platforms, list)
            or not source_platforms
            or not set(source_platforms).issubset(PLATFORMS)
        ):
            errors.append(f"{prefix}.platforms must be a non-empty supported-platform list")
        surfaces = source.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces or any(not _is_text(v) for v in surfaces):
            errors.append(f"{prefix}.surfaces must be a non-empty string list")
        for field in ("published_date", "updated_date"):
            _check_date(source.get(field), f"{prefix}.{field}", errors)
        _check_date(source.get("last_verified"), f"{prefix}.last_verified", errors, nullable=False)
        commit = source.get("version_commit")
        if commit is not None and (not isinstance(commit, str) or not SHA_RE.fullmatch(commit)):
            errors.append(f"{prefix}.version_commit must be a full lowercase commit SHA or null")
        if source.get("source_type") in {"official_source_code", "official_source_code_documentation"}:
            if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
                errors.append(f"{prefix} current code evidence requires a full commit SHA")
            elif commit not in str(url):
                errors.append(f"{prefix}.url must pin its version_commit")

    claim_by_id: dict[str, dict] = {}
    covered_platforms: set[str] = set()
    for index, claim in enumerate(claims):
        prefix = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _check_text_fields(
            claim,
            ("id", "platform", "surface", "stage", "claim", "evidence_class", "confidence", "scope", "limitations"),
            prefix,
            errors,
        )
        claim_id = claim.get("id")
        if isinstance(claim_id, str):
            if not ID_RE.fullmatch(claim_id):
                errors.append(f"{prefix}.id must use lowercase hyphen-case")
            elif claim_id in claim_by_id:
                errors.append(f"duplicate claim id: {claim_id}")
            else:
                claim_by_id[claim_id] = claim
        platform = claim.get("platform")
        if platform not in PLATFORMS:
            errors.append(f"{prefix}.platform is unsupported")
        else:
            covered_platforms.add(platform)
        if claim.get("evidence_class") not in EVIDENCE_CLASSES:
            errors.append(f"{prefix}.evidence_class is unsupported")
        if claim.get("confidence") not in CONFIDENCE:
            errors.append(f"{prefix}.confidence is unsupported")
        _check_date(claim.get("as_of"), f"{prefix}.as_of", errors, nullable=False)
        source_ids = claim.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids or any(not _is_text(v) for v in source_ids):
            errors.append(f"{prefix}.source_ids must be a non-empty string list")
            source_ids = []
        for source_id in source_ids:
            source = source_by_id.get(source_id)
            if source is None:
                errors.append(f"{prefix} references unknown source: {source_id}")
            elif platform in PLATFORMS and platform not in source.get("platforms", []):
                errors.append(f"{prefix} source {source_id} does not cover {platform}")
        code_paths = claim.get("code_paths")
        if not isinstance(code_paths, list) or any(not _is_text(v) for v in code_paths):
            errors.append(f"{prefix}.code_paths must be a string list")
            code_paths = []
        commit = claim.get("version_commit")
        if claim.get("evidence_class") == "confirmed_code":
            if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
                errors.append(f"{prefix} confirmed_code requires a full commit SHA")
            if not code_paths:
                errors.append(f"{prefix} confirmed_code requires code_paths")
            for source_id in source_ids:
                source = source_by_id.get(source_id, {})
                if source.get("version_commit") != commit:
                    errors.append(f"{prefix} commit does not match source {source_id}")
        elif commit is not None:
            errors.append(f"{prefix} non-code claim must use null version_commit")

    if covered_platforms != PLATFORMS:
        errors.append("claims must cover all six supported platforms")

    platform_dir = skill_root / "references" / "platforms"
    documented_claims: set[str] = set()
    for platform in sorted(PLATFORMS):
        path = platform_dir / f"{platform}.md"
        if not path.is_file():
            errors.append(f"missing platform reference: {path.relative_to(skill_root)}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in re.findall(r"`([^`]+)`", text):
            if ID_RE.fullmatch(token) and token.startswith(tuple(f"{p}-" for p in PLATFORMS) + ("meta-",)):
                if token not in claim_by_id:
                    errors.append(f"{path.name} references unknown claim: {token}")
                else:
                    documented_claims.add(token)
    undocumented = sorted(set(claim_by_id) - documented_claims)
    if undocumented:
        errors.append("registry claims not used by platform references: " + ", ".join(undocumented))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--skill-root", type=Path, default=default_root)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args(argv)
    registry = args.registry or args.skill_root / "references" / "source-registry.json"
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "errors": [f"registry unreadable: {exc}"]}, ensure_ascii=False))
        return 1
    errors = validate_registry(payload, args.skill_root)
    result = {
        "status": "passed" if not errors else "failed",
        "platforms": len(payload.get("platforms", [])),
        "sources": len(payload.get("sources", [])),
        "claims": len(payload.get("claims", [])),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
