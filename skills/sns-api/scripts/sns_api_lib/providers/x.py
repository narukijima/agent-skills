"""X API v2 adapter. X-specific text and reconciliation semantics stay here."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode, urlsplit

try:
    import fcntl
except ImportError:
    fcntl = None

from ..auth import fingerprint, provider_env
from ..core import ApiFailure, parse_time, utc_now
from ..http import classify, request
from .base import CredentialSnapshot, Provider

HOSTS = {"api.x.com"}
BASE = "https://api.x.com"
LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_PATTERN = re.compile(r"(?<![@A-Za-z0-9_])(?:https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?::[0-9]{1,5})?(?:/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*)?)", re.I)
CASHTAG = re.compile(r"(?<![A-Za-z0-9_])\$[A-Za-z][A-Za-z0-9_]*")


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.rstrip("\n"))


def content_hash(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode()).hexdigest()


def _url_spans(text: str):
    result = []
    for match in URL_PATTERN.finditer(text):
        end = match.end()
        while end > match.start():
            candidate = text[match.start():end]
            trailing = text[end - 1]
            if trailing in ".,!?;:>'\"、。！？，" or (trailing == ")" and candidate.count(")") > candidate.count("(")):
                end -= 1
            else:
                break
        result.append((match.start(), end))
    return result


def _emoji_end(text: str, start: int) -> int:
    cp = ord(text[start])
    if text[start] in "#*0123456789":
        index = start + 1
        if index < len(text) and text[index] == "\ufe0f": index += 1
        return index + 1 if index < len(text) and text[index] == "\u20e3" else start
    is_emoji = 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x1F1E6 <= cp <= 0x1F1FF
    if not is_emoji:
        return start
    index = start + 1
    if 0x1F1E6 <= cp <= 0x1F1FF and index < len(text) and 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
        return index + 1
    while index < len(text) and (text[index] in {"\ufe0e", "\ufe0f", "\u20e3"} or 0x1F3FB <= ord(text[index]) <= 0x1F3FF):
        index += 1
    while index < len(text) and (0xE0020 <= ord(text[index]) <= 0xE007E or ord(text[index]) == 0xE007F): index += 1
    while index + 1 < len(text) and text[index] == "\u200d" and (0x1F000 <= ord(text[index + 1]) <= 0x1FAFF or 0x2600 <= ord(text[index + 1]) <= 0x27BF):
        index += 2
        while index < len(text) and (text[index] in {"\ufe0e", "\ufe0f"} or 0x1F3FB <= ord(text[index]) <= 0x1F3FF):
            index += 1
    return index


def weighted_length(value: str) -> int:
    text = normalize_text(value)
    spans = iter(_url_spans(text)); current = next(spans, None)
    total = index = 0
    while index < len(text):
        if current and index == current[0]:
            total += 23; index = current[1]; current = next(spans, None); continue
        end = _emoji_end(text, index)
        if end > index:
            total += 2; index = end; continue
        cp = ord(text[index]); total += 1 if any(low <= cp <= high for low, high in LIGHT_RANGES) else 2; index += 1
    return total


def _quote_target(url: str) -> bool:
    parsed = urlsplit(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com")) and bool(re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/[0-9]+/?", parsed.path))


def validate_text(value: str) -> str:
    text = normalize_text(value)
    errors = []
    if not text.strip(): errors.append("TEXT_EMPTY")
    if weighted_length(text) > 280: errors.append("TEXT_TOO_LONG")
    if len(CASHTAG.findall(text)) > 1: errors.append("TOO_MANY_CASHTAGS")
    if any(_quote_target(text[a:b]) for a, b in _url_spans(text)): errors.append("UNDECLARED_QUOTE_TARGET")
    if any(unicodedata.category(ch).startswith("C") and ch not in {"\n", "\t", "\u200d", "\ufe0f"} for ch in text): errors.append("CONTROL_CHARACTER")
    if errors:
        raise ApiFailure("X post validation failed: " + ", ".join(errors), code="INVALID_CONTENT", payload={"errors": errors})
    return text


def percent(value: str) -> str:
    return quote(value, safe="")


def oauth1_header(method: str, url: str, params: Optional[Dict[str, str]], extra: Dict[str, str], nonce: Optional[str] = None, timestamp: Optional[str] = None) -> str:
    oauth = {"oauth_consumer_key": extra["api_key"], "oauth_nonce": nonce or secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": timestamp or str(int(time.time())),
             "oauth_token": extra["access_token"], "oauth_version": "1.0"}
    combined = dict(params or {}); combined.update(oauth)
    parameter = "&".join(k + "=" + v for k, v in sorted((percent(k), percent(str(v))) for k, v in combined.items()))
    base_string = "&".join((method.upper(), percent(url), percent(parameter)))
    key = percent(extra["api_secret"]) + "&" + percent(extra["access_token_secret"])
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base_string.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(percent(k) + '=\"' + percent(v) + '\"' for k, v in sorted(oauth.items()))


def _atomic_private(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp." + secrets.token_hex(8))
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, 0o600); json.dump(value, handle, sort_keys=True); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try: temp.unlink()
        except FileNotFoundError: pass


def _refresh_token(client_id: str, client_secret: str, store_name: str, bootstrap: str) -> str:
    if fcntl is None:
        raise ApiFailure("OAuth refresh locking unavailable", code="UNSAFE_OAUTH_REFRESH")
    store = Path(store_name); lock = store.with_name(store.name + ".lock"); marker = store.with_name(store.name + ".refresh-pending")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        os.chmod(lock, 0o600); fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                stored = json.loads(store.read_text()) if store.exists() else {}
                pending = json.loads(marker.read_text()) if marker.exists() else {}
            except (OSError, json.JSONDecodeError) as exc:
                raise ApiFailure("OAuth credential state is unreadable; reauthorization required", code="CREDENTIAL_STATE_UNKNOWN") from exc
            if pending and pending.get("rotation_id") != stored.get("last_rotation_id"):
                raise ApiFailure("OAuth refresh state unresolved; reauthorization required", code="CREDENTIAL_STATE_UNKNOWN")
            if pending:
                marker.unlink(missing_ok=True)
            try:
                if stored.get("access_token") and time.time() < float(stored.get("expires_at", 0)) - 60:
                    return str(stored["access_token"])
            except (TypeError, ValueError) as exc:
                raise ApiFailure("OAuth credential expiry is invalid; reauthorization required", code="CREDENTIAL_STATE_UNKNOWN") from exc
            refresh = stored.get("refresh_token") or bootstrap
            if not refresh: raise ApiFailure("OAuth refresh token unavailable", code="MISSING_CREDENTIAL")
            rotation = secrets.token_hex(16)
            try:
                _atomic_private(marker, {"rotation_id": rotation, "started_at": utc_now()})
            except OSError as exc:
                raise ApiFailure("could not persist OAuth refresh intent; no refresh request was sent", code="CREDENTIAL_STORE_UNAVAILABLE") from exc
            authorization = None; form = {"grant_type": "refresh_token", "refresh_token": refresh}
            if client_secret:
                authorization = "Basic " + base64.b64encode((client_id + ":" + client_secret).encode()).decode()
            else: form["client_id"] = client_id
            try:
                result = request("POST", BASE + "/2/oauth2/token", allowed_hosts=HOSTS, authorization=authorization, form=form)
            except ApiFailure as exc:
                if exc.status is not None and 400 <= exc.status < 500: marker.unlink(missing_ok=True)
                else: exc.code = "CREDENTIAL_STATE_UNKNOWN"
                raise
            token = result.body.get("access_token") if isinstance(result.body, dict) else None
            if not token: raise ApiFailure("OAuth refresh result unknown; reauthorization required", code="CREDENTIAL_STATE_UNKNOWN")
            try:
                _atomic_private(store, {"access_token": token, "expires_at": time.time() + float(result.body.get("expires_in", 7200)),
                                        "refresh_token": result.body.get("refresh_token") or refresh, "last_rotation_id": rotation})
            except (OSError, TypeError, ValueError) as exc:
                raise ApiFailure("OAuth refresh persistence result is unknown; reauthorization required", code="CREDENTIAL_STATE_UNKNOWN") from exc
            marker.unlink(missing_ok=True); return str(token)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class XProvider(Provider):
    name = "x"; account_type = "user"; legacy_read_gate = "X_API_READ_ENABLED"; legacy_write_gate = "X_POSTING_ENABLED"
    capabilities = ("identity.read", "user.lookup", "post.lookup", "user.posts", "post.search.recent", "usage.read", "publish.text", "reconcile", "manual.resolve")
    read_operations = ("identity.read", "user.lookup", "post.lookup", "user.posts", "post.search.recent", "usage.read")
    publish_operations = ("publish.text",); supports_manual_resolve = True

    def normalize_publish(self, operation, payload, assets):
        if assets: raise ApiFailure("X v1 publish.text does not accept media", code="INVALID_MEDIA")
        if set(payload) - {"text"}: raise ApiFailure("unsupported X publish field", code="INVALID_CONTENT")
        return {"text": validate_text(str(payload.get("text", "")))}

    def call_plan(self, operation, payload, assets):
        return {"max_calls": 3, "calls": ["POST /2/oauth2/token (conditional)", "GET /2/users/me", "POST /2/tweets"]}

    def credentials(self, for_write, operation=""):
        bearer = provider_env("x", "BEARER_TOKEN", legacy=["X_BEARER_TOKEN"])
        if not for_write and (operation == "usage.read" or (operation not in {"identity.read", "reconcile"} and bearer)):
            token = bearer or provider_env("x", "BEARER_TOKEN", legacy=["X_BEARER_TOKEN"], required=True)
            public = provider_env("x", "APP_PUBLIC_ID", legacy=["X_API_APP_ID"], required=True)
            return CredentialSnapshot("app", token, public, fingerprint("app", public))
        mapping = [("API_KEY", "X_API_KEY"), ("API_SECRET", "X_API_SECRET"), ("ACCESS_TOKEN", "X_ACCESS_TOKEN"), ("ACCESS_TOKEN_SECRET", "X_ACCESS_TOKEN_SECRET")]
        values = {name.lower(): provider_env("x", name, legacy=[old]) for name, old in mapping}
        if any(values[key] for key in ("api_key", "api_secret", "access_token_secret")):
            if not all(values.values()): raise ApiFailure("OAuth 1.0a variables must be complete", code="MISSING_CREDENTIAL")
            public = values["api_key"]
            return CredentialSnapshot("oauth1", values["access_token"], public, fingerprint("oauth1", public), values)
        client = provider_env("x", "OAUTH2_CLIENT_ID", legacy=["X_OAUTH2_CLIENT_ID", "X_OAUTH2_STATIC_CLIENT_ID"])
        store = provider_env("x", "OAUTH2_TOKEN_STORE", legacy=["X_OAUTH2_TOKEN_STORE"])
        if store:
            if not client: raise ApiFailure("OAuth2 token store requires client ID", code="MISSING_CREDENTIAL")
            token = _refresh_token(client, provider_env("x", "OAUTH2_CLIENT_SECRET", legacy=["X_OAUTH2_CLIENT_SECRET"]), store,
                                   provider_env("x", "OAUTH2_REFRESH_TOKEN", legacy=["X_OAUTH2_REFRESH_TOKEN"]))
        else:
            token = provider_env("x", "OAUTH2_ACCESS_TOKEN", legacy=["X_ACCESS_TOKEN"], required=True)
        if not client: raise ApiFailure("static OAuth2 token requires client ID", code="MISSING_CREDENTIAL")
        return CredentialSnapshot("oauth2", token, client, fingerprint("oauth2", client))

    def _call(self, credentials, method, path, query=None, body=None):
        auth = oauth1_header(method, BASE + path, {k: str(v) for k, v in (query or {}).items()}, credentials.extra) if credentials.auth_mode == "oauth1" else None
        result = request(method, BASE + path, allowed_hosts=HOSTS, token=None if auth else credentials.token,
                         authorization=auth, query=query, json_body=body)
        return result

    def identity(self, credentials):
        result = self._call(credentials, "GET", "/2/users/me", {"user.fields": "id,name,username"})
        data = result.body.get("data") if isinstance(result.body, dict) else None
        if not isinstance(data, dict) or not data.get("id"): raise ApiFailure("X identity response missing id", code="INVALID_PROVIDER_RESPONSE", outcome="unknown")
        return {**data, "account_type": "user"}

    def read_call_budget(self, operation, params, credentials):
        bearer = provider_env("x", "BEARER_TOKEN", legacy=["X_BEARER_TOKEN"])
        return 1 if operation == "usage.read" or (operation != "identity.read" and bearer) else 2

    def read(self, credentials, operation, params):
        if operation == "identity.read": path, query = "/2/users/me", {"user.fields": "id,name,username"}
        elif operation == "user.lookup":
            supplied = [name for name in ("username", "user_id") if params.get(name) not in (None, "")]
            if len(supplied) != 1: raise ApiFailure("user.lookup requires exactly one of username or user_id", code="INVALID_PARAMETER")
            if supplied[0] == "user_id": path = "/2/users/" + _numeric(params["user_id"], "user_id")
            else: path = "/2/users/by/username/" + quote(str(params["username"]), safe="")
            query = {"user.fields": "id,name,username,created_at,public_metrics"}
        elif operation == "post.lookup": path, query = "/2/tweets", {"ids": str(params.get("ids", "")), "tweet.fields": "created_at,entities,public_metrics"}
        elif operation == "user.posts": path, query = "/2/users/" + _numeric(params.get("user_id"), "user_id") + "/tweets", {"max_results": _range(params.get("max_results", 10), 5, 100), "pagination_token": params.get("next_token"), "tweet.fields": "created_at,entities"}
        elif operation == "post.search.recent": path, query = "/2/tweets/search/recent", {"query": str(params.get("query", "")), "max_results": _range(params.get("max_results", 10), 10, 100), "next_token": params.get("next_token"), "tweet.fields": "created_at,entities"}
        elif operation == "usage.read": path, query = "/2/usage/tweets", {}
        else: raise ApiFailure("unsupported X read", code="UNSUPPORTED_CAPABILITY")
        result = self._call(credentials, "GET", path, query)
        body = result.body if isinstance(result.body, dict) else {"data": result.body}
        return {"status": classify(body), "data": body.get("data"), "errors": body.get("errors", []),
                "rate_limit": result.rate_limit, "provider": {"meta": body.get("meta", {}), "includes": body.get("includes", {})}}

    def publish(self, credentials, manifest, checkpoint):
        result = self._call(credentials, "POST", "/2/tweets", body={"text": manifest["provider_payload"]["text"]})
        data = result.body.get("data") if isinstance(result.body, dict) else None
        if not isinstance(data, dict) or not data.get("id"): raise ApiFailure("X publish response missing post id", code="INVALID_PROVIDER_RESPONSE", status=result.status, outcome="unknown")
        checkpoint({"provider_id": str(data["id"]), "provider_status": "published"})
        return {"status": "published", "provider_id": str(data["id"]), "provider_status": "published",
                "http_status": result.status, "rate_limit": result.rate_limit, "provider": {"url": "https://x.com/i/web/status/" + str(data["id"])}}

    def reconcile_call_budget(self, row): return 2

    def reconcile(self, credentials, row):
        result = self._call(credentials, "GET", "/2/users/" + quote(row["account_id"], safe="") + "/tweets",
                            {"max_results": "100", "tweet.fields": "created_at,entities", "exclude": "replies,retweets"})
        posts = result.body.get("data") if isinstance(result.body, dict) else []
        attempted = parse_time(row["attempted_at"], "attempted_at"); expected = row["provider_payload"]["text"]
        for post in posts or []:
            if not isinstance(post, dict) or not post.get("created_at"): continue
            created = parse_time(str(post["created_at"]), "post created_at")
            if attempted - timedelta(seconds=30) <= created <= attempted + timedelta(minutes=5):
                variants = [str(post.get("text", "")), html.unescape(str(post.get("text", "")))]
                expanded = variants[-1]
                for item in ((post.get("entities") or {}).get("urls") or []):
                    expanded = expanded.replace(str(item.get("url", "")), str(item.get("expanded_url", "")))
                if content_hash(expected) in {content_hash(v) for v in [*variants, expanded]}:
                    return {"status": "confirmed_success", "provider_id": str(post.get("id")), "provider_status": "published"}
        timestamps = [parse_time(str(p["created_at"]), "created_at") for p in posts or [] if isinstance(p, dict) and p.get("created_at")]
        contains_url = bool(_url_spans(expected)); partial = bool(result.body.get("errors")) if isinstance(result.body, dict) else True
        if not contains_url and not partial and timestamps and min(timestamps) <= attempted <= max(timestamps):
            return {"status": "confirmed_absent", "provider": {"timeline_window_covered": True, "posts_examined": len(posts or [])}}
        return {"status": "unresolved", "provider": {"contains_url": contains_url, "partial_errors": partial, "posts_examined": len(posts or [])}}

    def valid_provider_id(self, value): return bool(value and str(value).isdigit())


def _numeric(value, label):
    if not str(value).isdigit(): raise ApiFailure(label + " must be a stable numeric X user ID", code="INVALID_PARAMETER")
    return str(value)


def _range(value, minimum, maximum):
    try: number = int(value)
    except (TypeError, ValueError) as exc: raise ApiFailure("max_results must be integer", code="INVALID_PARAMETER") from exc
    if not minimum <= number <= maximum: raise ApiFailure("max_results out of API range; refusing clamp", code="INVALID_PARAMETER")
    return str(number)
