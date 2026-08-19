# X Provider

## Contract

- Host: `https://api.x.com`
- Auth: OAuth 2.0 app bearer for supported public reads/usage where applicable; OAuth 1.0a User Context or OAuth 2.0 Authorization Code + PKCE User Context for identity/write.
- OAuth 2.0 scopes for this surface: minimize to `tweet.read`, `users.read`, and `tweet.write`; add `media.write` for media operations and `offline.access` only when the private refresh-store flow is used.
- Account binding: numeric stable X user ID from `GET /2/users/me`.

Reads cover user lookup, post lookup, user posts, recent search, usage, and media upload status. Writes cover standalone text, URL-based quote, 1–4 local images, one local MP4 video, and one local GIF. Reply, dedicated `quote_tweet_id`, delete, like, follow, DM, and browser posting remain unsupported.

`prepare` applies NFC normalization, twitter-text v3-style weighting, 23-character URLs, emoji clusters, the 280 weighted limit, control/cashtag checks, and `UNDECLARED_QUOTE_TARGET` rejection. To quote, select `publish.quote` and supply `quote_url`; the adapter canonicalizes it to `https://x.com/i/web/status/{id}` and places it in the signed text. It never sends a reply or `quote_tweet_id`. An X status URL hidden inside another operation remains rejected. This contract guarantees the URL in the Post text; X controls client-side quote-card rendering and may change it.

`publish.image` uses `POST /2/media/upload`, supports the currently documented image MIME/size boundary, and accepts optional one-to-one `alt_texts` through `POST /2/media/metadata`. `publish.video` supports one local `video/mp4`; `publish.gif` supports one local GIF. Both use the v2 initialize/append/finalize/status path with bounded chunks. All media IDs come from files whose canonical path, MIME, size, and SHA-256 are signed in the manifest; callers cannot inject an existing media ID. `made_with_ai` is an optional approval-bound boolean for media posts.

X may accept media while processing is still `pending` or `in_progress`; return `submitted`, persist only non-secret media IDs/keys/status, and resume only the exact signed manifest. Re-hash local bytes after upload and before Post creation. A checkpoint records `post_create_started` before `POST /2/tweets`: a crash proven to precede that checkpoint can reconcile as absent, while any uncertainty after it remains subject to timeline reconciliation and no blind retry.

X publish is immediate only when the response contains an unambiguous post ID. Reconciliation anchors on the durable Post-create timestamp, compares raw/HTML-unescaped/t.co-expanded text, verifies a URL quote's target ID through `referenced_tweets` or expanded URL evidence, and for media also requires exact uploaded media keys from `attachments.media_keys`. It may prove absence only when a complete returned timeline strictly brackets the attempt and the approved text has no URL. URL quotes remain unresolved unless matched or manually resolved with evidence. X and Facebook Pages are the only Providers that support audited manual resolve; X requires a numeric Post ID for a published resolution.

Official sources:

- [X API overview](https://docs.x.com/overview)
- [Authenticated user lookup](https://docs.x.com/x-api/users/lookup/quickstart/authenticated-lookup)
- [Manage Posts authentication mapping](https://docs.x.com/fundamentals/authentication/guides/v2-authentication-mapping)
- [Counting characters](https://docs.x.com/fundamentals/counting-characters)
- [Create Post](https://docs.x.com/x-api/posts/create-or-edit-post)
- [Media overview](https://docs.x.com/x-api/media/introduction)
- [Simple media upload](https://docs.x.com/x-api/media/upload-media)
- [Chunked media upload](https://docs.x.com/x-api/media/quickstart/media-upload-chunked)
- [Media metadata](https://docs.x.com/x-api/media/create-media-metadata)
- [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits)
- [Usage and billing](https://docs.x.com/x-api/fundamentals/usage-and-billing)
- [OAuth 2.0 Authorization Code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)

Pricing, plan availability, post constraints, and limits drift. Recheck official docs/Developer Console/headers at execution time.
