#!/usr/bin/env python3
"""Small, dependency-free X API v2 client with a guarded post path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


USER_FIELDS = "created_at,description,location,public_metrics,profile_image_url,protected,url,verified"
POST_FIELDS = "created_at,conversation_id,lang,possibly_sensitive,public_metrics"


class ApiFailure(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


def json_loads(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw_response": raw.decode("utf-8", errors="replace")[:2000]}


def token_for(kind: str) -> str:
    if kind == "user":
        value = os.environ.get("X_ACCESS_TOKEN", "")
        variable = "X_ACCESS_TOKEN"
    else:
        value = os.environ.get("X_BEARER_TOKEN", "")
        variable = "X_BEARER_TOKEN"
    if not value:
        raise ApiFailure("missing required environment variable: " + variable)
    return value


def choose_auth(operation: str, requested: str) -> str:
    if operation in {"me", "post"} and requested == "app":
        raise ApiFailure(operation + " requires user-context authentication; do not use --auth app")
    if requested in {"user", "app"}:
        return requested
    if operation in {"me", "post"}:
        return "user"
    if os.environ.get("X_BEARER_TOKEN"):
        return "app"
    return "user"


def api_request(
    method: str,
    path: str,
    auth_kind: str,
    params: Optional[Dict[str, str]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    base_url = os.environ.get("X_API_BASE_URL", "https://api.x.com").rstrip("/")
    query = ("?" + urlencode(params)) if params else ""
    request = Request(base_url + path + query, method=method)
    request.add_header("Authorization", "Bearer " + token_for(auth_kind))
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "agent-sills-x-api/0.1")
    if body is not None:
        request.add_header("Content-Type", "application/json")
        request.data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json_loads(response.read())
            if not 200 <= response.status < 300:
                raise ApiFailure("X API returned an error", response.status, payload)
            return payload
    except HTTPError as exc:
        payload = json_loads(exc.read())
        raise ApiFailure("X API returned an HTTP error", exc.code, payload) from exc
    except URLError as exc:
        raise ApiFailure("X API request result is unknown: " + str(exc.reason)) from exc
    except TimeoutError as exc:
        raise ApiFailure("X API request result is unknown: timeout") from exc


def print_json(value: Any, pretty: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_ledger(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApiFailure("invalid JSON in ledger: " + str(path)) from exc
        if isinstance(record, dict):
            records.append(record)
    return records


def append_ledger(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def post_text(args: argparse.Namespace) -> Any:
    if args.text is not None and args.file is not None:
        raise ApiFailure("use either --text or --file, not both")
    if args.text is None and args.file is None:
        raise ApiFailure("post requires --text or --file")
    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    text = text.rstrip("\n")
    if not text:
        raise ApiFailure("post text must not be empty")
    digest = content_sha256(text)
    if args.live and args.dry_run:
        raise ApiFailure("use either --live or --dry-run, not both")
    if not args.live or args.dry_run:
        result = {"dry_run": True, "content_sha256": digest, "text_length": len(text), "text": text}
        if args.content_id:
            result["content_id"] = args.content_id
        return result
    if os.environ.get("X_POSTING_ENABLED") != "true":
        raise ApiFailure("live posting requires X_POSTING_ENABLED=true")
    if not args.ledger or not args.content_id:
        raise ApiFailure("live posting requires --ledger and --content-id")

    ledger_path = Path(args.ledger)
    attempts = 0
    for record in read_ledger(ledger_path):
        if record.get("content_sha256") != digest and record.get("content_id") != args.content_id:
            continue
        attempts += 1
        if record.get("status") == "sent":
            raise ApiFailure("duplicate post refused: content is already marked sent")
        if record.get("status") == "unknown" and not args.retry_unknown:
            raise ApiFailure("unknown post result refused: inspect and explicitly use --retry-unknown")
    if attempts >= 2:
        raise ApiFailure("post attempt limit reached: maximum 2 attempts per content_id or content_sha256")

    record: Dict[str, Any] = {"attempted_at": utc_now(), "content_id": args.content_id, "content_sha256": digest, "status": "unknown"}
    try:
        payload = api_request("POST", "/2/tweets", "user", body={"text": text})
    except ApiFailure as exc:
        record["status"] = "unknown" if "unknown" in str(exc) else "failed"
        if exc.status is not None:
            record["http_status"] = exc.status
        append_ledger(ledger_path, record)
        raise
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or not payload["data"].get("id"):
        append_ledger(ledger_path, record)
        raise ApiFailure("post response did not contain a post id")
    post_id = str(payload["data"]["id"])
    record.update({"status": "sent", "post_id": post_id, "http_status": 201})
    append_ledger(ledger_path, record)
    return {"content_sha256": digest, "ledger": str(ledger_path), "post_id": post_id, "url": "https://x.com/i/web/status/" + post_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read X API v2 data or prepare a guarded text post.")
    parser.add_argument("--auth", choices=["auto", "app", "user"], default="auto", help="read auth mode; post always uses user context")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("me", help="get the authenticated user")
    user = sub.add_parser("user", help="get a user by username")
    user.add_argument("--username", required=True)
    by_id = sub.add_parser("user-by-id", help="get a user by id")
    by_id.add_argument("--user-id", required=True)
    posts = sub.add_parser("posts", help="get posts by user id")
    posts.add_argument("--user-id", required=True)
    posts.add_argument("--max-results", type=int, default=10)
    posts.add_argument("--pagination-token")
    tweet = sub.add_parser("post-by-id", help="get posts by comma-separated ids")
    tweet.add_argument("--ids", required=True)
    search = sub.add_parser("search-recent", help="search recent posts")
    search.add_argument("--query", required=True)
    search.add_argument("--max-results", type=int, default=10)
    search.add_argument("--next-token")
    post = sub.add_parser("post", help="dry-run by default; optionally send one text post")
    group = post.add_mutually_exclusive_group()
    group.add_argument("--text")
    group.add_argument("--file")
    post.add_argument("--live", action="store_true")
    post.add_argument("--dry-run", action="store_true", help="explicitly select the default non-sending mode")
    post.add_argument("--content-id")
    post.add_argument("--ledger")
    post.add_argument("--retry-unknown", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> Any:
    auth = choose_auth(args.command, args.auth)
    if args.command == "me":
        return api_request("GET", "/2/users/me", auth, {"user.fields": USER_FIELDS})
    if args.command == "user":
        return api_request("GET", "/2/users/by/username/" + quote(args.username, safe=""), auth, {"user.fields": USER_FIELDS})
    if args.command == "user-by-id":
        return api_request("GET", "/2/users/" + args.user_id, auth, {"user.fields": USER_FIELDS})
    if args.command == "posts":
        params = {"max_results": str(max(5, min(args.max_results, 100))), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.pagination_token:
            params["pagination_token"] = args.pagination_token
        return api_request("GET", "/2/users/" + args.user_id + "/tweets", auth, params)
    if args.command == "post-by-id":
        return api_request("GET", "/2/tweets", auth, {"ids": args.ids, "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS})
    if args.command == "search-recent":
        params = {"query": args.query, "max_results": str(max(10, min(args.max_results, 100))), "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS}
        if args.next_token:
            params["next_token"] = args.next_token
        return api_request("GET", "/2/tweets/search/recent", auth, params)
    if args.command == "post":
        return post_text(args)
    raise ApiFailure("unsupported command")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print_json(dispatch(args), args.pretty)
        return 0
    except ApiFailure as exc:
        detail = {"error": str(exc)}
        if exc.status is not None:
            detail["http_status"] = exc.status
        if exc.payload is not None:
            detail["response"] = exc.payload
        print_json(detail, True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
