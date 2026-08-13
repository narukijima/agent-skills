# Authentication and secrets

## Common controls

Use gateway-owned configuration:

- `SNS_API_READ_ENABLED`, `SNS_API_WRITE_ENABLED`
- `SNS_API_PROJECT_ID`, `SNS_API_AGENT_ID`
- `SNS_API_READ_MAX_CALLS`, `SNS_API_WRITE_MAX_CALLS`
- `SNS_API_DAILY_READ_CALL_LIMIT`, `SNS_API_DAILY_WRITE_CALL_LIMIT`
- `SNS_API_MANIFEST_SIGNING_KEY` (minimum 32 bytes)
- `SNS_API_TEST_MODE` (loopback-only transport tests while writes are disabled)

Write invocation budget must equal the signed Provider call-plan maximum. Reads require at least the planned calls. Reserve budget before credential refresh or external requests.

## Provider namespaces

- X: `SNS_X_*`
- YouTube: `SNS_YOUTUBE_*`
- Facebook: `SNS_FACEBOOK_*`
- Instagram: `SNS_INSTAGRAM_*`
- Threads: `SNS_THREADS_*`

Every write Provider requires `SNS_<PROVIDER>_APP_ID` for the operator label and a public client/app identity used to derive `sha256(auth_mode + ":" + public_id)`. Tokens and secrets are never fingerprint inputs stored directly.

X supports migration aliases for the former `X_*` variables. New names win only when the old value is absent or identical; conflicting old/new values fail closed without printing either value. Deprecated controls include `X_API_READ_ENABLED`, `X_POSTING_ENABLED`, `X_API_*_MAX_CALLS`, Project/Agent/daily limits, `X_API_APP_ID`, `X_API_MANIFEST_SIGNING_KEY`, OAuth 1.0a variables, bearer token, and OAuth 2.0 variables.

X OAuth 2.0 refresh uses a private store, file lock, write-ahead non-secret rotation marker, atomic 0600 replacement, and no automatic retry when rotation result is unknown. Initial browser consent/PKCE callback/code exchange remains outside this Skill.

YouTube uses a pre-provisioned OAuth 2.0 user access token and client ID. The surrounding private token service owns refresh/rotation. Facebook uses a Page Access Token. Instagram requires explicit `SNS_INSTAGRAM_AUTH_MODE=facebook-login|instagram-login`; the modes have different hosts, permissions, and token issuance. Threads uses a Threads OAuth 2.0 user token.

## Secret boundary

Never place a secret in CLI arguments, provider payload, remote-media URL, manifest, SQLite, logs, fixtures, stdout, or stderr. The runtime rejects configured secret values found in the manifest and recursively redacts Provider errors. It also drops keys named `access_token`/`refresh_token` from audit/output structures. Keep real credentials out of CI.

Credential bootstrap, browser consent, Meta app review, business verification, Page/Professional-account assignment, and token issuance belong to operator provisioning, not runtime execution.
