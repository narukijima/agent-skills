# Threads Provider

## Contract

- Host/version: `https://graph.threads.net/v1.0` (Threads version track is separate from Meta Graph `v26.0`).
- Auth: Threads OAuth 2.0 user token.
- Minimum scopes for this surface: `threads_basic` and `threads_content_publish`.
- Account binding: `GET /me` stable Threads user ID.

Reads cover identity and own Threads posts. Publishing covers text, remote image, remote video, and carousel. Create child/parent containers, check `id,status,error_message`, and call `/{threads-user-id}/threads_publish` only when `FINISHED`. Preserve `IN_PROGRESS`, `FINISHED`, `PUBLISHED`, `ERROR`, and `EXPIRED` separately.

Persist `creating_children → creating_parent` (or `creating_container`) `→ container_created → processing → ready → final_publish_started → published`, and reuse checkpointed carousel child IDs. A container-creation timeout/crash is pre-publish: after a grace-window reconciliation it can become resumable `submitted`, even if an orphan non-public container must be recreated. A timeout during `threads_publish` remains `unknown`; do not repeat it. Reconcile a lost final response only through a unique owned-post match using signed text, native media type, and attempt window.

Remote media is fetched by Threads and may mutate after prepare. The Skill records URL/host/expected MIME but does not claim byte-level assurance. If a submitted state outlives its manifest, use a fresh state-bound `prepare-resume` manifest under the same Domain Authorization reference rather than extending the old signature.

Official sources:

- [Threads API overview](https://developers.facebook.com/docs/threads/)
- [Threads publishing](https://developers.facebook.com/docs/threads/posts/)
- [Threads API changelog](https://developers.facebook.com/docs/threads/changelog)
- [Meta official Threads Postman workspace](https://www.postman.com/meta/threads/overview)
- [Official Postman Threads API documentation](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)

Scopes, container limits, quotas, media restrictions, and status behavior drift. Recheck official docs/App Dashboard/responses before live use.
