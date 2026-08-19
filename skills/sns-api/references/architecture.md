# Architecture

## Contents

- Boundaries
- Common lifecycle
- Provider contract
- State and outcomes
- Extending a provider

## Boundaries

The Skill implementation is a Common Safety Core plus static, thin Provider adapters. The Core owns workspace resolution, manifests, Domain Authorization/account/app/credential binding, budgets, ledger transactions, duplicate control, audit events, secret redaction, HTTP safety, media evidence, response envelopes, and dispatch. A Provider owns hosts, pinned version policy, credentials, authenticated identity, capability validation, API request shape, pagination, upload/publish protocol, native status, response normalization, reconciliation, and rate/quota metadata. Neither layer owns shell, filesystem, network, sandbox, or provider Runtime permission.

`scripts/sns_api.py` is only the CLI. `scripts/sns_api_lib/core.py` orchestrates the lifecycle. `providers/base.py` is a shallow contract, not an inheritance framework. `providers/__init__.py` is a fixed registry; it never dynamically imports caller-selected code. X keeps auth/text/reconcile in `providers/x.py` and its upload/checkpoint protocol in the provider-owned `providers/x_media.py`; this is a static responsibility split, not a plugin system.

## Common lifecycle

1. `capabilities` reads the fixed registry without credentials or network.
2. `read` validates a named capability, reserves invocation/daily budget, resolves one credential snapshot, and calls the Provider.
3. `prepare` normalizes one provider payload, validates exact-intent or standing Domain Authorization, captures asset evidence and call plan, and creates a short-lived signed manifest.
4. `send` verifies manifest/HMAC/expiry/assets, Domain Authorization scope, application kill switch, exact call budget, app ID, credential fingerprint, authenticated identity, and account type.
5. The ledger transaction records `unknown` before the first irreversible request.
6. Provider checkpoints persist non-secret stage/container IDs or an opaque private-session handle before later phases. Capability-sensitive session URLs stay outside SQLite.
7. A definite response becomes `published`, `submitted`, `failed`, or `rate_limited`. An uncertain response remains `unknown`.
8. `status` exposes native async state. `reconcile` uses official Provider evidence and never blindly resends.

YouTube, Instagram, Threads, and X media may return `submitted`. YouTube resumes the same private session URL from the Provider-acknowledged byte offset. Instagram/Threads persist `creating_children`, `creating_parent`/`creating_container`, `processing`, `ready`, `final_publish_started`, and `published`; checkpointed carousel children are reused. A pre-publish uncertainty can be converted from `unknown` to resumable `submitted` only by reconciliation after a grace window. A timeout during the final publish call remains `unknown` and requires official recent-content/status reconciliation.

If the short-lived manifest expires during `submitted`, `prepare-resume` creates a fresh HMAC manifest under the same opaque Domain Authorization reference. It binds the prior manifest hash and the current Provider-state hash, carries no content override, and cannot create a new intent. Any state change between prepare and send fails closed.

## State and outcomes

Canonical files:

- `state/sns-api/ledger.sqlite3`: publish intents, attempts, Provider checkpoints, and audit events.
- `state/sns-api/usage.sqlite3`: UTC-day call reservations keyed by platform, Project, Agent, and read/write kind.
- `state/sns-api/private/youtube-upload-sessions/*.json`: owner-only 0600 capability state under 0700 directories; never manifest/SQLite/output content.

Ledger uniqueness includes `(platform, account_id, content_id)` and `(platform, account_id, intent_hash)`, where `intent_hash` binds normalized provider payload plus asset metadata. A write-ahead attempt is committed with `unknown` before dispatch. SQLite uses WAL, FULL synchronous mode, `BEGIN IMMEDIATE`, and bounded lock retry.

Common states:

| State | Meaning |
| --- | --- |
| `prepared` | Signed local intent only; no provider request. |
| `unknown` | An irreversible request may have reached the Provider; blind retry forbidden. |
| `submitted` | Provider accepted a stable upload/container/object, but full publication/processing is not complete. |
| `published` | Provider-specific evidence says the content is published/complete. |
| `failed` | A definite failure; one exact-intent retry may reuse the same still-valid Domain Authorization. |
| `rate_limited` | 429 or equivalent; preserve rate metadata, persist the reset, and refuse new calls locally until it passes. |
| `confirmed_absent` | Provider-specific reconciliation proved absence. |
| `unresolved` | Available official evidence cannot prove success or absence. |

Store native status separately as `provider_status`; never force YouTube processing, Meta containers, and X immediate post into one native vocabulary.

## Single-host boundary

The nearest `.git` file/directory defines the workspace root. Missing markers fail closed. SQLite serializes cooperating processes sharing one state file; it does not protect against hostile code that can edit the database or against independent machines. Put credentials, signing key, budgets, runtime, and state behind a dedicated single-writer gateway/central state for unattended multi-machine operation.

## Extending a provider

Add a static module and registry entry. Declare only real capabilities. Implement normalization, call plan, credential snapshot/fingerprint, identity, read/publish, and reconciliation. Add official source links and cover the new provider in the safety-core tests. Do not add a generic endpoint escape hatch or a universal social-object schema.
