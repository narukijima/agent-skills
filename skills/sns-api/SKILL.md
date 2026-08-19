---
name: sns-api
description: Use only when explicitly requested to read or safely publish via official X (including URL quotes and local media), YouTube, Facebook Pages, Instagram Professional, or Threads APIs.
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "3.0.0"
  agent-directory.status: "active"
  agent-directory.aliases: "sns api,social api,social media api,x-api,x api,twitter-api"
---

# sns-api

## Purpose

Execute a small, allowlisted official-API surface for X, YouTube, Facebook Pages, Instagram Professional accounts, and Threads. Let the Project decide content, timing, account, and Domain Authorization. Own only validation, authentication binding, budget enforcement, immutable media evidence, provider dispatch, canonical state, duplicate prevention, and uncertain-result recovery.

Treat one publish intent as exactly `1 platform × 1 account × 1 content intent`. Let the caller orchestrate cross-posting with independent manifests. Never expose `send-all`, `post-everywhere`, or a distributed cross-platform transaction.

TikTok is `planned`; do not claim runtime support.

## Activation

- Require explicit `$sns-api` invocation for paid/external reads and every external write.
- Do not treat the aliases `x-api`, `x api`, or `twitter-api` as permission to bypass the `sns-api` workflow.
- A bound scheduler or content pipeline may invoke `$sns-api` under standing authorization; do not add a repeated Human Approval when its signed Domain scope still matches.

## 使用するKnowledge

### Required

- なし。

### Conditional

Read only what the requested operation needs:

- Read `references/architecture.md` for Common Core lifecycle, state, or extension work.
- Read `references/capability-matrix.md` before selecting an operation or handling unsupported capability.
- Read `references/auth-and-secrets.md` for credentials, scopes, or token rotation.
- Read `references/publishing-safety.md` for `prepare`, `send`, `reconcile`, manual resolve, media, or async states.
- Read `references/providers/x.md` only for X.
- Read `references/providers/youtube.md` only for YouTube.
- Read `references/providers/facebook.md` only for Facebook Pages.
- Read `references/providers/instagram.md` only for Instagram.
- Read `references/providers/threads.md` only for Threads.
- Read `references/providers/tiktok.md` only for future TikTok planning or an unsupported request.

API versions, scopes, quotas, prices, rate limits, and restrictions drift. Recheck the linked official docs, provider console, app review state, and response headers at execution time. Do not replace the pinned, tested API version with automatic `latest` selection.

## Safety boundary

- Do not inspect, set, or emulate Generic Runtime Permission. Shell, filesystem, network, sandbox, and provider execution-mode failures belong to the Runtime layer.
- Require `SNS_API_READ_ENABLED=true` for external reads and `SNS_API_WRITE_ENABLED=true` for writes.
- Treat these read/write gates as SNS application kill switches, not as Runtime network permission or Domain Authorization.
- Require Project/Agent invocation and daily call budgets before any credential refresh or provider request.
- Accept secrets only from environment variables or a gateway-owned private token store. Never accept them as CLI arguments or place them in a prompt, manifest, ledger, stdout, stderr, fixture, or audit detail.
- Require a short-lived HMAC-SHA256 manifest signed with gateway-owned `SNS_API_MANIFEST_SIGNING_KEY`.
- Treat `approval-id` as a compatibility name for an opaque Domain Authorization reference, never as evidence that a Human approved shell execution.
- Bind platform, operation, normalized payload, assets, stable expected account ID/type, app ID, credential fingerprint, Domain Authorization, expiry, and provider call plan.
- For standing authorization, require its gateway HMAC and also bind allowed content source, Project/Agent caller, schedule, validity period, and per-intent/daily provider-call limits. Require every field to match; do not add per-intent Human Approval while it remains in scope.
- Let `prepare` accept provider JSON. Let `send` accept only `--manifest`; never add send-time platform, account, text, caption, media, or ledger overrides.
- Verify authenticated identity, account type, app label, and credential fingerprint before writing an attempt.
- Store canonical single-host state only under `state/sns-api/ledger.sqlite3` and `state/sns-api/usage.sqlite3`, resolved from the nearest `.git` marker. Do not accept arbitrary state paths.
- Write the attempt as `unknown` before the first irreversible provider request.
- Block blind retry of `unknown`, and block new sends to the same platform/account while an unknown remains.
- Treat timeout, disconnect, authenticated redirect, 5xx, or missing provider ID after an irreversible/public request as `unknown`; treat definite 4xx as `failed`; preserve 429/rate metadata without automatic sleep. A Provider may return resumable `submitted` only when its checkpoint proves no public publish started or preserves a recoverable upload session.
- Reconcile through the provider's official status/read surface. Never infer `confirmed_absent` unless the provider-specific evidence proves absence.
- Enable audited manual resolve only for X and Facebook Pages, with gateway privilege, out-of-band evidence, reason, and provider ID when resolving as published.
- Verify local asset canonical path, MIME, size, and SHA-256 immediately before send. Refuse mutation.
- For X, upload only manifest-bound local images/video/GIF through the fixed `/2/media/*` surface, checkpoint non-secret media IDs/keys, and reverify bytes before `POST /2/tweets`. Never accept caller-supplied media IDs.
- For X quotes, require `publish.quote` with an approved `quote_url`; canonicalize that URL into the signed Post text. Do not expose reply automation or the dedicated `quote_tweet_id` field.
- Keep YouTube resumable session URLs only in canonical 0700/0600 private state; store only an opaque handle/hash in SQLite. Authenticate every session PUT, probe with empty PUT plus `Content-Range: bytes */TOTAL`, and resume from the server `Range`.
- For Instagram and Threads, persist explicit pre-publish and final-publish stages. A pre-publish container uncertainty may become resumable only after reconciliation; uncertainty after `media_publish`/`threads_publish` starts remains `unknown`.
- Treat remote media as mutable after prepare. Record URL, scheme, host, expected MIME and optional expected metadata; do not claim byte-level assurance when the provider fetches it.
- Reject authenticated redirects and non-allowlisted credential destinations. Do not expose a generic URL or endpoint CLI.
- Keep local SQLite's boundary explicit: it serializes cooperating processes on one workspace/host; it is not global uniqueness across machines. Use a dedicated single-writer gateway/central state for unattended multi-machine operation.

## Workflow

### Inspect capabilities

```bash
python3 skills/sns-api/scripts/sns_api.py capabilities
python3 skills/sns-api/scripts/sns_api.py capabilities --platform instagram
```

### Read

Choose one registry operation and bounded provider parameters. Never pass an endpoint or arbitrary URL.

```bash
SNS_API_READ_ENABLED=true SNS_API_READ_MAX_CALLS=2 \
SNS_API_PROJECT_ID=project-1 SNS_API_AGENT_ID=agent-1 \
SNS_API_DAILY_READ_CALL_LIMIT=100 \
python3 skills/sns-api/scripts/sns_api.py read \
  --platform x --operation user.posts \
  --params '{"user_id":"123456789","max_results":5}'
```

### Sign a standing authorization

For a bound scheduler or content pipeline, sign the Project-defined scope once with the gateway-owned key. Do not reimplement the canonical-JSON HMAC.

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py sign-standing-authorization \
  --scope-file .tmp/standing-scope.json \
  --output .tmp/standing-signed.json
```

### Prepare

Issue exactly one Project-approved provider payload. Put media descriptors in `assets` inside the JSON payload. Compute the expected credential fingerprint from the public app/client identity, never the token.

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform threads --operation publish.text \
  --payload '{"text":"approved text"}' \
  --manifest .tmp/approved-threads.json \
  --content-id content-2026-08-13-001 \
  --expected-account-id 123456789 \
  --account-type threads-user \
  --app-id threads-production \
  --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-2026-08-13-001
```

For an X URL quote, use `publish.quote`; `prepare` appends the canonical Post URL to the signed text. For X media, provide local assets. Images may include matching `alt_texts`; videos and GIFs use chunked upload and may return `submitted` while X processing continues.

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform x --operation publish.quote \
  --payload '{"text":"approved comment","quote_url":"https://x.com/example/status/123456789"}' \
  --manifest .tmp/approved-x-quote.json \
  --content-id content-2026-08-14-quote \
  --expected-account-id 123456789 --account-type user \
  --app-id x-production --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-2026-08-14-quote
```

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform x --operation publish.image \
  --payload '{"text":"approved caption","assets":[{"kind":"local","path":"/absolute/photo.png","mime":"image/png"}],"alt_texts":["Approved image description"]}' \
  --manifest .tmp/approved-x-image.json \
  --content-id content-2026-08-14-001 \
  --expected-account-id 123456789 --account-type user \
  --app-id x-production --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-2026-08-14-001
```

### Send

Set the exact signed call-plan budget for the manifest. `send` may safely resume a recoverable YouTube upload session, an already-created Instagram/Threads container, or an X media object still processing from canonical state; it never recreates content after a final publish request became unknown.

```bash
SNS_API_WRITE_ENABLED=true SNS_API_WRITE_MAX_CALLS=4 \
SNS_API_PROJECT_ID=project-1 SNS_API_AGENT_ID=agent-1 \
SNS_API_DAILY_WRITE_CALL_LIMIT=50 \
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py send \
  --manifest .tmp/approved-threads.json
```

If a submitted upload/container outlives its manifest, prepare a new short-lived manifest bound to the current canonical Provider state under the same Domain Authorization reference. This command accepts no content override and cannot create a new intent.

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare-resume \
  --manifest .tmp/expired-submitted.json \
  --resume-manifest .tmp/approved-resume.json
```

### Status and reconcile

Use `status` for provider-native processing/container state. Use `reconcile` for canonical `unknown`/`submitted` ledger outcomes. Do not resend first.

```bash
SNS_API_READ_ENABLED=true SNS_API_READ_MAX_CALLS=1 \
SNS_API_PROJECT_ID=project-1 SNS_API_AGENT_ID=agent-1 \
SNS_API_DAILY_READ_CALL_LIMIT=100 \
python3 skills/sns-api/scripts/sns_api.py status \
  --platform youtube --resource-id VIDEO_ID

SNS_API_READ_ENABLED=true SNS_API_READ_MAX_CALLS=2 \
SNS_API_PROJECT_ID=project-1 SNS_API_AGENT_ID=agent-1 \
SNS_API_DAILY_READ_CALL_LIMIT=100 \
python3 skills/sns-api/scripts/sns_api.py reconcile \
  --platform x --content-id content-2026-08-13-001 \
  --expected-account-id 123456789
```

## Output contract

Return a machine-readable envelope with `status`, `platform`, `operation`, `data`, `errors`, and `_meta`. Preserve provider-native pagination/status metadata under `_meta.provider`. Preserve budget, rate/quota headers, auth mode, and request time. Keep `partial`, `empty`, `failed`, `rate_limited`, `unknown`, `unresolved`, `submitted`, and `published` distinct.

## Prohibitions

- No generic REST client, arbitrary provider path, arbitrary host, dynamic provider loading, or browser fallback.
- No unofficial/private API, DM, ads, follow, like, delete, full analytics, scheduler, daemon, content generation, caption generation, or posting strategy.
- No raw send payload, unsigned manifest, caller-selected ledger, or multi-platform atomic publish.
- No automatic retry after an irreversible request may have reached a provider.
- No claim that container/upload acceptance equals complete publication.
