# Threads Provider

## Contract

- Host/version: `https://graph.threads.net/v1.0` (Threads version track is separate from Meta Graph `v26.0`).
- Auth: Threads OAuth 2.0 user token. Long-lived tokens officially expire after 60 days; the official refresh surface is `GET https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token`. The Skill accepts a pre-provisioned token and does not refresh; the operator owns rotation before expiry.
- Minimum scopes for this surface: `threads_basic` and `threads_content_publish`.
- Account binding: `GET /me` stable Threads user ID.

Reads cover identity, own Threads posts, and the official publishing quota (`GET /{threads-user-id}/threads_publishing_limit?fields=quota_usage,config`; profiles are officially capped at API-published posts per rolling 24 hours — check it before batches instead of publishing blind into the cap). Publishing covers text, remote image, remote video, and carousel. `alt_text` is accepted only for image/video publishes (officially an image/video accessibility field, max 1000 characters). Create child/parent containers, check `id,status,error_message`, and call `/{threads-user-id}/threads_publish` only when `FINISHED`. Preserve `IN_PROGRESS`, `FINISHED`, `PUBLISHED`, `ERROR`, and `EXPIRED` separately. `PUBLISHED` is terminal: the adapter never repeats `threads_publish` on a published container; a published container without a known final post ID resolves through reconcile.

Meta signals rate limiting with official error codes (4, 17, 32, 613, 80000-80014) on 400-class responses, not HTTP 429. The shared Meta layer classifies those as `rate_limited` and feeds `X-Business-Use-Case-Usage` `estimated_time_to_regain_access` into the local reset gate, matching the official "stop calling until access returns" guidance.

Persist `creating_children → creating_parent` (or `creating_container`) `→ container_created → processing → ready → final_publish_started → published`, and reuse checkpointed carousel child IDs. A container-creation timeout/crash is pre-publish: after a grace-window reconciliation it can become resumable `submitted`, even if an orphan non-public container must be recreated. A timeout during `threads_publish` remains `unknown`; do not repeat it. Reconcile a lost final response only through a unique owned-post match using signed text, native media type, and attempt window.

Remote media is fetched by Threads and may mutate after prepare. The Skill records URL/host/expected MIME but does not claim byte-level assurance. If a submitted state outlives its manifest, use a fresh state-bound `prepare-resume` manifest under the same Domain Authorization reference rather than extending the old signature.

Official sources:

- [Threads API overview](https://developers.facebook.com/docs/threads/)
- [Threads publishing](https://developers.facebook.com/docs/threads/posts/)
- [Threads API changelog](https://developers.facebook.com/docs/threads/changelog)
- [Meta official Threads Postman workspace](https://www.postman.com/meta/threads/overview)
- [Official Postman Threads API documentation](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)

Scopes, container limits, quotas, media restrictions, and status behavior drift. Recheck official docs/App Dashboard/responses before live use.
