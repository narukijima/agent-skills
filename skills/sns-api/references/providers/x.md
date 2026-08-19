# X Provider

## Contract

- Host: `https://api.x.com`
- Auth: OAuth 2.0 app bearer for supported public reads/usage where applicable; OAuth 1.0a User Context or OAuth 2.0 Authorization Code + PKCE User Context for identity/write.
- OAuth 2.0 scopes for this surface: minimize to `tweet.read`, `users.read`, and `tweet.write`; add `media.write` for media operations and `offline.access` only when the private refresh-store flow is used.
- Account binding: numeric stable X user ID from `GET /2/users/me`.

Reads cover user lookup, post lookup, user posts, recent search, usage, and media upload status. Writes cover standalone text, URL-based quote, 1–4 local images, one local MP4 video, and one local GIF. Reply, dedicated `quote_tweet_id`, delete, like, follow, DM, and browser posting remain unsupported.

`prepare` applies NFC normalization, twitter-text v3-style weighting, 23-character URLs, emoji clusters, the 280 weighted limit, control/cashtag checks, and `UNDECLARED_QUOTE_TARGET` rejection. To quote, select `publish.quote` and supply `quote_url`; the adapter canonicalizes it, extracts the numeric Post ID, and sends it in the official `quote_tweet_id` request field of `POST /2/tweets`. The comment text never embeds the quoted URL. It never sends a reply. An X status URL inside any Post text remains rejected: URL-in-text quoting is unofficial, ambiguous to reconcile, and billed by X as a URL Post at a far higher per-request price than a plain create.

`publish.image` uses `POST /2/media/upload`, supports the currently documented image MIME/size boundary, and accepts optional one-to-one `alt_texts` through `POST /2/media/metadata`. `publish.video` supports one local `video/mp4`; `publish.gif` supports one local GIF. Both use the v2 initialize/append/finalize/status path with bounded chunks. All media IDs come from files whose canonical path, MIME, size, and SHA-256 are signed in the manifest; callers cannot inject an existing media ID. `made_with_ai` is an optional approval-bound boolean for media posts.

X may accept media while processing is still `pending` or `in_progress`; return `submitted`, persist only non-secret media IDs/keys/status, and resume only the exact signed manifest. Re-hash local bytes after upload and before Post creation. A checkpoint records `post_create_started` before `POST /2/tweets`: a crash proven to precede that checkpoint can reconcile as absent, while any uncertainty after it remains subject to timeline reconciliation and no blind retry.

X publish is immediate only when the response contains an unambiguous post ID. Reconciliation anchors on the durable Post-create timestamp, compares raw/HTML-unescaped/t.co-expanded text, verifies a quote's target ID through `referenced_tweets` (type `quoted`) or expanded URL evidence, and for media also requires exact uploaded media keys from `attachments.media_keys`. It may prove absence only when a complete returned timeline strictly brackets the attempt and the approved text has no URL. X and Facebook Pages are the only Providers that support audited manual resolve; X requires a numeric Post ID for a published resolution.

## Pay-per-use billing

X bills API usage per request/resource with prepaid credits ([official pricing](https://docs.x.com/x-api/getting-started/pricing)). Consequences this Skill enforces or that the Project must plan for:

- Reads are billed per returned resource (posts ≈ $0.005/post, users ≈ $0.010/user, owned reads ≈ $0.001/resource at the time of writing; recheck the official page). `GET /2/users/me` before every send/reconcile is a billed read. `reconcile` reads up to 100 owned timeline posts per run — run it once per uncertain intent, never in a loop.
- Post creation is billed per request; a Post whose text contains a URL is billed at a much higher per-request price than a plain create. Quotes therefore use `quote_tweet_id`, never URL-in-text.
- Failed cycles still cost money. The Skill refuses locally — before any billable call — when the ledger would reject the send or a recorded 429 window is still open.
- On HTTP 429, the official guidance is to wait until `x-rate-limit-reset` (or `retry-after`) before retrying. The Skill records that reset and refuses further sends/reads for the platform until it passes; do not work around this gate.
- Monitor spend with `usage.read` (`GET /2/usage/tweets`) and the Developer Console before and after any batch.

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
- [Pay-per-usage pricing and credits](https://docs.x.com/x-api/getting-started/pricing)
- [OAuth 2.0 Authorization Code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)

Pricing, plan availability, post constraints, and limits drift. Recheck official docs/Developer Console/headers at execution time.
