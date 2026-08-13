# Publishing safety

## Manifest

Schema v2 binds:

- schema version, platform, operation, content ID
- stable expected account ID and account type
- operator app ID and expected credential fingerprint
- approval ID, creation, expiry
- normalized Provider payload and SHA-256
- local/remote asset metadata and aggregate hash
- Provider call plan and maximum
- whole-manifest SHA-256 and HMAC-SHA256

`prepare` is the only path that accepts content. `send` accepts one manifest path and no override. The HMAC binds an approval reference; it does not replace the Project approval system. Keep the signing key outside general Agents.

## Media

For local assets, resolve a canonical non-symlink regular file and record MIME, size, and SHA-256. Re-resolve and re-hash before credential use or send. The duplicate intent hash binds normalized provider payload and asset metadata, so equal captions with different assets remain distinct intents. YouTube streams the verified file to its resumable session without reading the whole asset into memory. Treat its resumable session URI as capability-sensitive: store only its SHA-256 checkpoint, never the URI itself.

For remote assets, require HTTPS without URL userinfo, record host and expected MIME, and optionally record size/hash/ETag/Last-Modified supplied by the Project. Instagram, Threads, and supported Facebook media operations let the Provider fetch the URL. The Skill cannot prove the fetched bytes remained identical after prepare; `mutable_after_prepare: true` documents this limit. Do not add generic hosting.

## Attempt lifecycle

Identity and credential checks happen before the attempt. The irreversible lifecycle is:

1. `BEGIN IMMEDIATE` duplicate/unknown/account check.
2. Commit `unknown` attempt and audit event.
3. Provider may checkpoint a session/container ID without secrets.
4. Dispatch the write.
5. Record definite `published`, `submitted`, `failed`, or `rate_limited`; keep uncertain results `unknown`.

Process death after step 2 leaves `unknown`, so another process cannot duplicate the post. Any `unknown` for a platform/account blocks other new content for that same platform/account.

## Retry and resume

Do not retry timeout, disconnect, authenticated redirect, 5xx, malformed success, or missing Provider ID. Run `reconcile`. A definite 4xx becomes `failed`; retry requires a newly signed approval ID. A 429 does not consume the publish attempt but still consumes the conservative daily call reservation; retry only after external rate policy allows it.

Instagram/Threads `submitted` containers may be resumed by the same exact signed manifest because the container ID is durable and the adapter does not recreate it. Other Providers reject repeated sends after `submitted`. Once a final publish request becomes unknown, resume is disabled and reconcile is mandatory.

## Manual resolve

Only X declares `manual.resolve`. Require the gateway-owned signing key, an `unknown` ledger row, detailed out-of-band evidence/reason, and a numeric post ID for `published`. Store an immutable `manual-resolve` audit event. Never edit/delete/replace SQLite to bypass the gate.
