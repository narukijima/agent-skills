# Architecture

## Contents

- Boundaries
- Common lifecycle
- Provider contract
- State and outcomes
- Extending a provider

## Boundaries

The runtime is a Common Safety Core plus static, thin Provider adapters. The Core owns workspace resolution, manifests, approval/account/app/credential binding, budgets, ledger transactions, duplicate control, audit events, secret redaction, HTTP safety, media evidence, response envelopes, and dispatch. A Provider owns hosts, pinned version policy, credentials, authenticated identity, capability validation, API request shape, pagination, upload/publish protocol, native status, response normalization, reconciliation, and rate/quota metadata.

`scripts/sns_api.py` is only the CLI. `scripts/sns_api_lib/core.py` orchestrates the lifecycle. `providers/base.py` is a shallow contract, not an inheritance framework. `providers/__init__.py` is a fixed registry; it never dynamically imports caller-selected code. X keeps auth/text/reconcile in `providers/x.py` and its upload/checkpoint protocol in the provider-owned `providers/x_media.py`; this is a static responsibility split, not a plugin system.

## Common lifecycle

1. `capabilities` reads the fixed registry without credentials or network.
2. `read` validates a named capability, reserves invocation/daily budget, resolves one credential snapshot, and calls the Provider.
3. `prepare` normalizes one provider payload, captures asset evidence and call plan, and creates a short-lived signed manifest.
4. `send` verifies manifest/HMAC/expiry/assets, write gate, exact call budget, app ID, credential fingerprint, authenticated identity, and account type.
5. The ledger transaction records `unknown` before the first irreversible request.
6. Provider checkpoints persist non-secret container IDs or one-way session hashes before later phases.
7. A definite response becomes `published`, `submitted`, `failed`, or `rate_limited`. An uncertain response remains `unknown`.
8. `status` exposes native async state. `reconcile` uses official Provider evidence and never blindly resends.

Instagram and Threads may return `submitted` while a container is `IN_PROGRESS`; X video/GIF may do the same while uploaded media is processing. A second `send --manifest` is allowed only for the exact same signed manifest and only when the canonical checkpoint proves that the same container/media object already exists; the adapter checks/finalizes it without recreating content. A timeout during the final publish call becomes `unknown`, which disables this resume path and requires reconciliation.

## State and outcomes

Canonical files:

- `state/sns-api/ledger.sqlite3`: publish intents, attempts, Provider checkpoints, and audit events.
- `state/sns-api/usage.sqlite3`: UTC-day call reservations keyed by platform, Project, Agent, and read/write kind.

### Legacy X upgrade guard

Before X send/reconcile/resolve or X call-budget reservation, the Core checks only the canonical legacy files `state/x-api/x-posts.sqlite3` and `state/x-api/x-usage.sqlite3`. A valid v2 post ledger is copied transactionally into SNS ledger schema v3: `sent` becomes `published`, `unknown` remains `unknown`, and text/content hashes become duplicate tombstones. Each row receives a `legacy-x-migration` event and source-row mapping. Existing SNS state is merged only when account, content ID, payload hash, and intent hash agree; a mismatch fails closed.

Usage rows are added once to any already-reserved SNS X calls so an upgrade cannot reset the daily Project/Agent budget. Both migrations store a canonical source snapshot digest. Repeated execution is idempotent. A malformed source or any source change after migration blocks X with structured `LEGACY_X_STATE_*` errors. Stop the old runtime before migration; SQLite cannot make two independent runtimes on different state files one distributed transaction.

Ledger uniqueness includes `(platform, account_id, content_id)` and `(platform, account_id, intent_hash)`, where `intent_hash` binds normalized provider payload plus asset metadata. A write-ahead attempt is committed with `unknown` before dispatch. SQLite uses WAL, FULL synchronous mode, `BEGIN IMMEDIATE`, and bounded lock retry.

Common states:

| State | Meaning |
| --- | --- |
| `prepared` | Signed local intent only; no provider request. |
| `unknown` | An irreversible request may have reached the Provider; blind retry forbidden. |
| `submitted` | Provider accepted a stable upload/container/object, but full publication/processing is not complete. |
| `published` | Provider-specific evidence says the content is published/complete. |
| `failed` | A definite failure; retry requires a new signed approval. |
| `rate_limited` | 429 or equivalent; preserve rate metadata and do not sleep automatically. |
| `confirmed_absent` | Provider-specific reconciliation proved absence. |
| `unresolved` | Available official evidence cannot prove success or absence. |

Store native status separately as `provider_status`; never force YouTube processing, Meta containers, and X immediate post into one native vocabulary.

## Single-host boundary

The nearest `.git` file/directory defines the workspace root. Missing markers fail closed. SQLite serializes cooperating processes sharing one state file; it does not protect against hostile code that can edit the database or against independent machines. Put credentials, signing key, budgets, runtime, and state behind a dedicated single-writer gateway/central state for unattended multi-machine operation.

## Extending a provider

Add a static module and registry entry. Declare only real capabilities. Implement normalization, call plan, credential snapshot/fingerprint, identity, read/publish, and reconciliation. Add official source links, request-shape tests, async/unknown tests, capability-doc consistency tests, and behavior eval cases. Do not add a generic endpoint escape hatch or a universal social-object schema.
