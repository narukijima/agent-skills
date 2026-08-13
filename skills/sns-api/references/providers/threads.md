# Threads Provider

## Contract

- Host/version: `https://graph.threads.net/v1.0` (Threads version track is separate from Meta Graph `v26.0`).
- Auth: Threads OAuth 2.0 user token.
- Minimum scopes for this surface: `threads_basic` and `threads_content_publish`.
- Account binding: `GET /me` stable Threads user ID.

Reads cover identity and own Threads posts. Publishing covers text, remote image, remote video, and carousel. Create child/parent containers, check `id,status,error_message`, and call `/{threads-user-id}/threads_publish` only when `FINISHED`. Preserve `IN_PROGRESS`, `FINISHED`, `PUBLISHED`, `ERROR`, and `EXPIRED` separately.

Remote media is fetched by Threads and may mutate after prepare. The Skill records URL/host/expected MIME but does not claim byte-level assurance. A known `IN_PROGRESS` container can be resumed with the same exact signed manifest; a timeout during `threads_publish` becomes `unknown` and cannot be blindly resumed.

Official sources:

- [Threads API overview](https://developers.facebook.com/docs/threads/)
- [Threads publishing](https://developers.facebook.com/docs/threads/posts/)
- [Threads API changelog](https://developers.facebook.com/docs/threads/changelog)
- [Meta official Threads Postman workspace](https://www.postman.com/meta/threads/overview)
- [Official Postman Threads API documentation](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)

Scopes, container limits, quotas, media restrictions, and status behavior drift. Recheck official docs/App Dashboard/responses before live use.
