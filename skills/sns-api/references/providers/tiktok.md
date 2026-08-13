# TikTok integration point — planned

TikTok is not registered as a runtime-supported Provider. `capabilities --platform tiktok` returns `status: planned` and `runtime_supported: false`; every runtime operation fails closed.

Future integration should implement a static adapter for `open.tiktokapis.com`, Creator identity/account binding, `video.upload`/`video.publish` scope separation, creator-info validation, Direct Post versus inbox-upload semantics, FILE_UPLOAD/PULL_FROM_URL media transfer, `publish_id` checkpointing, polling/webhook status, and moderation/private-mode restrictions. It must reuse the Common manifest, media evidence, ledger, budget, host allowlist, and unknown lifecycle without introducing a generic endpoint path.

TikTok's current official Content Posting API supports Direct Post and upload-to-inbox flows and now documents photo publishing. Unaudited clients and users/scopes have material visibility/review restrictions. Status includes processing/upload/download, inbox, complete, and failure states; a `publish_id` is not equivalent to public completion.

Official sources to recheck when implementing:

- [Content Posting API product](https://developers.tiktok.com/products/content-posting-api)
- [Get started](https://developers.tiktok.com/doc/content-posting-api-get-started)
- [Upload video](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video)
- [Get post status](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status)
- [Media transfer guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide)

Do not copy current endpoints/limits into runtime until implementation, official review, mock request-shape tests, safety tests, and capability/docs consistency are complete.
