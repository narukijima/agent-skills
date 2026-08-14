# Publishing safety

## Manifest

Schema v3 binds:

- schema version, platform, operation, content ID
- stable expected account ID and account type
- operator app ID and expected credential fingerprint
- opaque Domain Authorization reference, authorization type/scope, creation, expiry
- normalized Provider payload and SHA-256
- local/remote asset metadata and aggregate hash
- Provider call plan and maximum
- whole-manifest SHA-256 and HMAC-SHA256

`prepare` is the only path that accepts content. `send` accepts one manifest path and no override. `approval-id` is the compatibility CLI/ledger name for an opaque reference proving that the external-effect intent is authorized under the Project's operating contract. It is not a shell permission receipt and does not replace the Project authorization system. Keep the signing key outside general Agents.

Schema v2 manifests remain loadable for in-flight compatibility. New manifests use v3 and include `domain_authorization`.

## Standing authorization

A Project may supply a gateway-signed standing authorization JSON to `prepare`. It is not a bearer object accepted by `send`; the gateway HMAC is verified and the exact scope is copied into the short-lived HMAC manifest. Required fields are:

```json
{
  "schema_version": 1,
  "authorization_type": "standing",
  "authorization_id": "opaque-project-reference",
  "platform": "x",
  "operations": ["publish.text"],
  "expected_account_id": "123456789",
  "account_type": "user",
  "app_id": "x-production",
  "expected_credential_fingerprint": "<sha256>",
  "allowed_content_sources": ["pipeline:editorial-approved"],
  "max_provider_calls_per_intent": 3,
  "daily_write_call_limit": 20,
  "caller_scope": {
    "project_id": "project-1",
    "agent_id": "publisher-1",
    "schedule_id": "schedule:daily"
  },
  "not_before": "2026-08-01T00:00:00Z",
  "expires_at": "2026-09-01T00:00:00Z",
  "authorization_hash": "<sha256>",
  "hmac_signature": "<hmac-sha256>"
}
```

The hash covers canonical JSON excluding `authorization_hash` and `hmac_signature`; the HMAC covers canonical JSON excluding only `hmac_signature`, using the gateway-owned manifest signing key. `prepare --standing-authorization-file ... --content-source ...` fails closed unless both integrity values and platform, account/type, app, operation, credential identity, content source, per-intent and daily call budgets, caller, schedule, and validity all match. `send` rechecks the signed snapshot against current caller/schedule/budget variables before credentials or an external write. Matching scope proceeds without per-intent Human Approval. Any changed field is a different or out-of-scope intent.

## Media

For local assets, resolve a canonical non-symlink regular file and record MIME, size, and SHA-256. Re-resolve and re-hash before credential use or send. The duplicate intent hash binds normalized provider payload and asset metadata, so equal captions with different assets remain distinct intents. YouTube streams the verified file in bounded ranges without loading the whole asset. Every session probe/media PUT is authenticated. Treat the session URI as capability-sensitive: keep it only in canonical 0700/0600 private state, while SQLite stores an opaque handle/hash/offset and never the URI.

X accepts only local media for this Skill. Images use the fixed v2 simple upload; video and GIF use initialize/append/finalize/status with bounded base64 chunks. Record only media IDs, media keys, segment count, processing state, metadata progress, and whether Post creation durably started. Reverify every asset after upload and before `POST /2/tweets`, detecting mutation during a long upload. Optional image `alt_texts` are approval-bound and written through `/2/media/metadata`; `made_with_ai` is also approval-bound. Never accept pre-uploaded caller media IDs because they are not bound to the manifest asset hashes.

For remote assets, require HTTPS without URL userinfo, record host and expected MIME, and optionally record size/hash/ETag/Last-Modified supplied by the Project. Instagram, Threads, and supported Facebook media operations let the Provider fetch the URL. The Skill cannot prove the fetched bytes remained identical after prepare; `mutable_after_prepare: true` documents this limit. Do not add generic hosting.

## Attempt lifecycle

Before the first upgraded X attempt, legacy `sent` and `unknown` rows are imported as `published` and `unknown` respectively. This happens before credentials or provider dispatch, preserving both duplicate rejection and account-level unknown blocking. Migration is an audited canonical-state transition, not an external publish attempt.

Identity and credential checks happen before the attempt. The irreversible lifecycle is:

1. `BEGIN IMMEDIATE` duplicate/unknown/account check.
2. Commit `unknown` attempt and audit event.
3. Provider may checkpoint a session/container ID without secrets.
4. Dispatch the write.
5. Record definite `published`, `submitted`, `failed`, or `rate_limited`; keep uncertainty after a public/final write as `unknown`.

Process death after step 2 leaves `unknown`, so another process cannot duplicate the post. Any `unknown` for a platform/account blocks other new content for that same platform/account.

## Retry and resume

Do not retry timeout, disconnect, authenticated redirect, 5xx, malformed success, or missing Provider ID. Run `reconcile`. A definite 4xx becomes `failed`; one exact-intent retry may reuse the same Domain Authorization reference and remains bounded by the attempt and call budgets. A 429 does not consume the publish attempt but still consumes the conservative daily call reservation; retry only after external rate policy allows it.

YouTube upload, Instagram/Threads containers, and X media may be resumed by the exact signed manifest while it is valid. YouTube probes the same private session and obeys the Provider `Range`; Instagram/Threads reuse every checkpointed child/parent container. A pre-publish container request may have created an orphan object, but it cannot have published content: after a five-minute anti-race grace window, `reconcile` may convert that `unknown` to resumable `submitted`. The final `media_publish`/`threads_publish` checkpoint is a hard boundary; after it, official recent-content reconciliation is required and blind retry stays forbidden.

Do not weaken manifest expiry. If processing outlives it, `prepare-resume` requires the current submitted ledger row, an expired-or-current signed manifest matching the row, the same Domain Authorization reference, and a hash of the exact Provider state. The resume manifest carries no content/media override and cannot create a new intent. State drift after prepare fails closed. The legacy `authorize-resume` command name remains an alias, but it does not imply a new Human Approval.

An X crash durably known to precede `POST /2/tweets` can reconcile as `confirmed_absent`; once the `post_create_started` checkpoint is written, timeline reconciliation or audited manual resolve is required. Facebook Page writes checkpoint their attempt timestamp; lost responses are compared against recent Page content where possible.

## Manual resolve

Only X and Facebook Pages declare `manual.resolve`. Require the gateway-owned signing key, an `unknown` ledger row, detailed out-of-band evidence/reason, and a Provider-valid ID for `published`. Store an immutable `manual-resolve` audit event. Never edit/delete/replace SQLite to bypass the gate. Instagram/Threads pre-publish uncertainty uses stage-aware reconciliation; their final-publish uncertainty uses owned-content reconciliation, not generic manual clearing.
