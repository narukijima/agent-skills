#!/usr/bin/env python3
"""CLI for the sns-api Common Safety Core and fixed provider registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from sns_api_lib import core


def _payload(args: Any) -> Dict[str, Any]:
    if args.payload is not None:
        return core.json_object(args.payload, "--payload")
    try:
        value = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise core.ApiFailure("--payload-file must contain a JSON object", code="INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise core.ApiFailure("--payload-file must contain a JSON object", code="INVALID_JSON")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Use fixed SNS provider capabilities through signed manifests and canonical safety state.")
    value.add_argument("--pretty", action="store_true")
    commands = value.add_subparsers(dest="command", required=True)
    caps = commands.add_parser("capabilities", help="print the machine-readable provider capability registry")
    caps.add_argument("--platform")
    commands.add_parser(
        "migrate-legacy-x",
        help="import canonical x-api ledger and usage safety state into sns-api without credentials or network",
    )
    read = commands.add_parser("read", help="run one allowlisted provider read capability")
    read.add_argument("--platform", required=True)
    read.add_argument("--operation", required=True)
    read.add_argument("--params", default="{}", help="provider-specific JSON object; never an endpoint or URL")
    prepare = commands.add_parser("prepare", help="validate and sign one platform/account/content publish intent")
    prepare.add_argument("--platform", required=True)
    prepare.add_argument("--operation", required=True)
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload", help="provider-specific JSON content specification")
    source.add_argument("--payload-file")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--content-id", required=True)
    prepare.add_argument("--expected-account-id", required=True)
    prepare.add_argument("--account-type")
    prepare.add_argument("--app-id", required=True)
    prepare.add_argument("--expected-credential-fingerprint", required=True)
    prepare.add_argument("--approval-id", required=True)
    prepare.add_argument("--expires-in", type=int, default=900, choices=range(60, 3601), metavar="60-3600")
    send = commands.add_parser("send", help="send or safely resume only the exact signed manifest intent")
    send.add_argument("--manifest", required=True)
    status = commands.add_parser("status", help="read a provider-native asynchronous publish status")
    status.add_argument("--platform", required=True)
    status.add_argument("--operation", required=True)
    status.add_argument("--resource-id", required=True)
    reconcile = commands.add_parser("reconcile", help="reconcile one unknown canonical-ledger outcome")
    reconcile.add_argument("--platform", required=True)
    reconcile.add_argument("--content-id", required=True)
    reconcile.add_argument("--expected-account-id", required=True)
    resolve = commands.add_parser("resolve", help="manual resolve only where the provider declares this capability")
    resolve.add_argument("--platform", required=True)
    resolve.add_argument("--content-id", required=True)
    resolve.add_argument("--expected-account-id", required=True)
    resolve.add_argument("--outcome", required=True, choices=["published", "confirmed_absent"])
    resolve.add_argument("--provider-id")
    resolve.add_argument("--reason", required=True)
    return value


def dispatch(args: Any) -> Dict[str, Any]:
    if args.command == "capabilities": return core.capabilities(args.platform)
    if args.command == "migrate-legacy-x": return core.migrate_legacy_x()
    if args.command == "read": return core.read(args.platform, args.operation, core.json_object(args.params, "--params"))
    if args.command == "prepare": args.payload = _payload(args); return core.prepare(args)
    if args.command == "send": return core.send(Path(args.manifest))
    if args.command == "status": return core.status(args.platform, args.operation, args.resource_id)
    if args.command == "reconcile": return core.reconcile(args.platform, args.content_id, args.expected_account_id)
    if args.command == "resolve": return core.resolve(args.platform, args.content_id, args.expected_account_id, args.outcome, args.reason, args.provider_id)
    raise core.ApiFailure("unsupported command", code="UNSUPPORTED_COMMAND")


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = core.redact(dispatch(args))
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 2 if result.get("status") in {"partial", "failed", "unknown", "unresolved"} else 0
    except core.ApiFailure as exc:
        platform = str(getattr(args, "platform", "") or "")
        operation = str(getattr(args, "operation", "") or args.command)
        error: Dict[str, Any] = core.envelope(
            platform, operation, status_value=exc.outcome or "failed", data={},
            errors=[{"code": exc.code, "message": str(exc)}],
        )
        if exc.status is not None: error["errors"][0]["http_status"] = exc.status
        if exc.payload is not None: error["errors"][0]["provider"] = exc.payload
        if exc.meta: error["_meta"].update(exc.meta)
        print(json.dumps(core.redact(error), ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        platform = str(getattr(args, "platform", "") or "")
        operation = str(getattr(args, "operation", "") or args.command)
        status_value = "unknown" if args.command in {"send", "reconcile", "status", "read"} else "failed"
        error = core.envelope(
            platform, operation, status_value=status_value, data={},
            errors=[{"code": "INTERNAL_ERROR", "message": "internal runtime failure; no secret details were emitted"}],
        )
        print(json.dumps(error, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
