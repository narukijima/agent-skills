#!/usr/bin/env python3
"""Extract bounded SEO evidence and audit an existing URL inventory.

This tool does not crawl the web and does not infer ranking impact or root cause.
It turns saved artifacts into reproducible observations and deterministic signals.
"""

from __future__ import annotations

import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit


VERSION = "0.2.0"
SEVERITY_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}


def canonical_url(value: str) -> str:
    """Normalize only comparison-safe URL components; preserve path and query."""
    without_fragment = urldefrag(value.strip()).url
    parsed = urlsplit(without_fragment)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    return urlunsplit((scheme, hostname, parsed.path or "/", parsed.query, ""))


def robots_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            for token in str(item).lower().replace(";", ",").split(","):
                stripped = token.strip().split(":", 1)[0]
                if stripped:
                    tokens.add(stripped)
    return tokens


def json_types(value: Any) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            kind = item.get("@type")
            if isinstance(kind, str):
                found.add(kind)
            elif isinstance(kind, list):
                found.update(str(entry) for entry in kind)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


class PageSignalParser(HTMLParser):
    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.title_parts: list[str] = []
        self.in_title = False
        self.heading_stack: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.canonical: list[str] = []
        self.hreflang: list[dict[str, str]] = []
        self.meta_robots: dict[str, list[str]] = {}
        self.links: list[str] = []
        self.language: str | None = None
        self.script_json_ld = False
        self.script_parts: list[str] = []
        self.json_ld: list[dict[str, Any]] = []

    @staticmethod
    def attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = self.attrs_dict(attrs)
        if tag == "html" and values.get("lang"):
            self.language = values["lang"]
        if tag == "title":
            self.in_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_stack.append(tag)
            self.headings.append({"level": tag, "text": ""})
        if tag == "meta":
            name = values.get("name", "").lower()
            if name == "robots" or "bot" in name or "crawler" in name:
                self.meta_robots.setdefault(name, []).append(values.get("content", ""))
        if tag == "link":
            rel = {part.lower() for part in values.get("rel", "").split()}
            href = values.get("href")
            if href and "canonical" in rel:
                self.canonical.append(urljoin(self.page_url, href))
            if href and "alternate" in rel and values.get("hreflang"):
                self.hreflang.append(
                    {"hreflang": values["hreflang"], "url": urljoin(self.page_url, href)}
                )
        if tag == "a" and values.get("href"):
            absolute = urljoin(self.page_url, values["href"])
            if urlsplit(absolute).scheme in {"http", "https"}:
                self.links.append(absolute)
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.script_json_ld = True
            self.script_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.heading_stack:
            self.heading_stack.pop()
        if tag == "script" and self.script_json_ld:
            raw = "".join(self.script_parts).strip()
            try:
                parsed = json.loads(raw)
                self.json_ld.append({"valid_json": True, "types": json_types(parsed)})
            except json.JSONDecodeError as error:
                self.json_ld.append(
                    {
                        "valid_json": False,
                        "error": f"{error.msg} at line {error.lineno} column {error.colno}",
                    }
                )
            self.script_json_ld = False
            self.script_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.heading_stack and self.headings:
            self.headings[-1]["text"] += data
        if self.script_json_ld:
            self.script_parts.append(data)

    def result(self) -> dict[str, Any]:
        origin = urlsplit(self.page_url)
        internal_links = sorted(
            {
                urldefrag(link).url
                for link in self.links
                if urlsplit(link).hostname == origin.hostname
            }
        )
        return {
            "tool": "seo_evidence",
            "version": VERSION,
            "evidence_scope": "saved_static_html",
            "page_url": self.page_url,
            "language": self.language,
            "title": " ".join("".join(self.title_parts).split()),
            "canonical": sorted(set(self.canonical)),
            "meta_robots": self.meta_robots,
            "headings": [
                {"level": item["level"], "text": " ".join(item["text"].split())}
                for item in self.headings
            ],
            "hreflang": self.hreflang,
            "internal_links": internal_links,
            "structured_data": {
                "status": "observed_in_static_html" if self.json_ld else "not_observed_in_static_html",
                "json_ld_blocks": self.json_ld,
                "limitation": (
                    "Absence in this saved static HTML does not establish absence from rendered DOM."
                    if not self.json_ld
                    else "Rendered output and consumer eligibility still require separate verification."
                ),
            },
        }


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {number}: {error.msg}") from error
            if not isinstance(item, dict):
                raise ValueError(f"line {number} must be a JSON object")
            records.append(item)
        return records
    if isinstance(value, dict):
        value = value["records"] if isinstance(value.get("records"), list) else [value]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("input must be a JSON array, {records: [...]}, or JSON Lines objects")
    return value


def signal(
    record: dict[str, Any], code: str, issue: str, evidence: str, impact: str, severity: str
) -> dict[str, Any]:
    return {
        "code": code,
        "url": record.get("url"),
        "issue": issue,
        "evidence": evidence,
        "impact": impact,
        "evidence_state": "observed",
        "severity_candidate": severity,
        "needs_diagnosis": True,
    }


def audit_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    url = record.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("every inventory record requires a non-empty url")
    status = record.get("status")
    if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
        raise ValueError(f"status must be an integer for {url}")

    findings: list[dict[str, Any]] = []
    in_sitemap = record.get("in_sitemap") is True
    expected_indexable = record.get("expected_indexable") is True or in_sitemap
    scope = record.get("scope", "url")
    crawler_role = record.get("crawler_role")
    tokens = robots_tokens(record.get("meta_robots"), record.get("x_robots_tag"))

    if crawler_role == "search" and status in {401, 403}:
        severity = "Critical" if scope == "site" else "High"
        findings.append(
            signal(
                record,
                "SEARCH_CRAWLER_BLOCKED",
                "Search crawler receives an access-denied response",
                f"verified inventory observation: status={status}, crawler_role=search, scope={scope}",
                "Important content may be unavailable for discovery, refresh, or rendering.",
                severity,
            )
        )
    if crawler_role == "search" and status == 429:
        findings.append(
            signal(
                record,
                "SEARCH_CRAWLER_THROTTLED",
                "Search crawler receives HTTP 429",
                "verified inventory observation: status=429 for crawler_role=search",
                "Repeated throttling can reduce crawl coverage or freshness.",
                "High",
            )
        )
    if crawler_role == "search" and isinstance(status, int) and status >= 500:
        severity = "Critical" if scope == "site" else "High"
        findings.append(
            signal(
                record,
                "SEARCH_CRAWLER_SERVER_ERROR",
                "Search crawler receives a server error",
                f"verified inventory observation: status={status}, scope={scope}",
                "The crawler cannot reliably fetch the affected content.",
                severity,
            )
        )

    redirect_chain = record.get("redirect_chain")
    if redirect_chain is not None:
        if not isinstance(redirect_chain, list) or any(not isinstance(item, str) for item in redirect_chain):
            raise ValueError(f"redirect_chain must be a string list for {url}")
        normalized_chain = [canonical_url(item) for item in redirect_chain]
        if len(set(normalized_chain)) != len(normalized_chain):
            findings.append(
                signal(
                    record,
                    "REDIRECT_LOOP",
                    "Redirect chain repeats a URL",
                    " -> ".join(redirect_chain),
                    "Crawlers and users may not reach a terminal document.",
                    "Critical" if scope == "site" else "High",
                )
            )
        elif len(redirect_chain) > 2:
            findings.append(
                signal(
                    record,
                    "REDIRECT_CHAIN",
                    "URL requires multiple redirect hops",
                    " -> ".join(redirect_chain),
                    "Extra hops consume requests and increase failure and latency risk.",
                    "Medium",
                )
            )

    if expected_indexable and record.get("robots_allowed") is False:
        findings.append(
            signal(
                record,
                "EXPECTED_URL_ROBOTS_BLOCKED",
                "Expected indexable URL is disallowed for the measured crawler",
                "robots_allowed=false with expected_indexable=true",
                "The crawler may be unable to fetch content and page-level index controls.",
                "Critical" if scope == "site" else "High",
            )
        )
    if expected_indexable and "noindex" in tokens:
        findings.append(
            signal(
                record,
                "EXPECTED_URL_NOINDEX",
                "Expected indexable URL declares noindex",
                f"observed robots tokens: {', '.join(sorted(tokens))}",
                "The URL is not eligible to remain indexed when the directive is processed.",
                "Critical" if scope == "site" else "High",
            )
        )

    declared = record.get("canonical")
    expected_canonical = record.get("expected_canonical")
    final_url = record.get("final_url") or url
    if declared is not None and not isinstance(declared, str):
        raise ValueError(f"canonical must be a string for {url}")
    if expected_canonical is not None and not isinstance(expected_canonical, str):
        raise ValueError(f"expected_canonical must be a string for {url}")
    if declared and expected_canonical and canonical_url(declared) != canonical_url(expected_canonical):
        findings.append(
            signal(
                record,
                "CANONICAL_MISMATCH",
                "Declared canonical differs from the Project's expected canonical",
                f"declared={declared}; expected={expected_canonical}",
                "Canonicalization signals may consolidate the URL to the wrong document.",
                "High",
            )
        )
    if in_sitemap and isinstance(status, int) and 300 <= status < 400:
        findings.append(
            signal(
                record,
                "SITEMAP_REDIRECT",
                "Sitemap URL redirects",
                f"status={status}; final_url={final_url}",
                "The sitemap does not directly enumerate the terminal URL.",
                "High",
            )
        )
    if in_sitemap and isinstance(status, int) and status >= 400:
        findings.append(
            signal(
                record,
                "SITEMAP_ERROR_URL",
                "Sitemap URL returns an error",
                f"status={status}",
                "The submitted URL cannot serve an indexable document.",
                "High",
            )
        )
    if in_sitemap and "noindex" in tokens:
        findings.append(
            signal(
                record,
                "SITEMAP_NOINDEX",
                "Sitemap contains a noindex URL",
                f"observed robots tokens: {', '.join(sorted(tokens))}",
                "Discovery and indexation signals conflict.",
                "High",
            )
        )
    if in_sitemap and declared and canonical_url(declared) != canonical_url(str(final_url)):
        findings.append(
            signal(
                record,
                "SITEMAP_NON_CANONICAL",
                "Sitemap URL declares another canonical URL",
                f"sitemap_url={url}; final_url={final_url}; canonical={declared}",
                "The sitemap and canonicalization signals identify different preferred URLs.",
                "High",
            )
        )
    if record.get("soft_404") is True:
        findings.append(
            signal(
                record,
                "SOFT_404_CANDIDATE",
                "Inventory marks the URL as a soft-404 candidate",
                "soft_404=true",
                "The URL may return a successful status without useful page content.",
                "Medium",
            )
        )
    return findings


def audit_inventory(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    count = 0
    for record in records:
        count += 1
        signals.extend(audit_record(record))
    severity_counts = {severity: 0 for severity in SEVERITY_RANK}
    for item in signals:
        severity_counts[item["severity_candidate"]] += 1
    return {
        "tool": "seo_evidence",
        "version": VERSION,
        "evidence_scope": "provided_url_inventory",
        "records": count,
        "signals": signals,
        "summary": {"signal_count": len(signals), "by_severity_candidate": severity_counts},
        "limitation": (
            "Signals are deterministic expected-state conflicts. Confirm population scope, root cause, final severity, "
            "fix, and verification in the target Project before changing production."
        ),
    }


def command_extract_html(args: argparse.Namespace) -> int:
    parser = PageSignalParser(args.url)
    parser.feed(args.html.read_text(encoding="utf-8"))
    print(json.dumps(parser.result(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_audit_inventory(args: argparse.Namespace) -> int:
    result = audit_inventory(load_records(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on == "none":
        return 0
    threshold = SEVERITY_RANK[args.fail_on]
    return 1 if any(SEVERITY_RANK[item["severity_candidate"]] >= threshold for item in result["signals"]) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-html", help="Extract observations from saved static HTML")
    extract.add_argument("--url", required=True, help="Absolute URL represented by the saved HTML")
    extract.add_argument("--html", required=True, type=Path, help="Path to a UTF-8 HTML file")
    extract.set_defaults(func=command_extract_html)

    inventory = subparsers.add_parser(
        "audit-inventory", help="Audit an existing JSON / JSONL URL inventory without network access"
    )
    inventory.add_argument("--input", required=True, type=Path)
    inventory.add_argument(
        "--fail-on",
        choices=["none", "Low", "Medium", "High", "Critical"],
        default="none",
        help="Return exit 1 when a signal at or above this candidate severity exists",
    )
    inventory.set_defaults(func=command_audit_inventory)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
