# Instagram Provider

## Contract

- Pinned version: `v26.0`.
- Hosts/auth modes: `graph.facebook.com` with Facebook Login, or `graph.instagram.com` with Instagram Login. Set `SNS_INSTAGRAM_AUTH_MODE` explicitly; never auto-collapse the modes.
- Accounts: Instagram Professional Business/Creator only.
- Facebook Login permissions for this surface commonly include `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, and Page discovery/assignment permissions such as `pages_show_list` when needed.
- Instagram Login uses its separate permissions, including `instagram_business_basic` and `instagram_business_content_publish` for this surface.

Reads cover Professional identity and owned media. Publishing covers remote image, video, Reel, and 2–10 item carousel. Every publish creates Provider containers, checks `status_code`, and calls `media_publish` only when ready. Preserve `IN_PROGRESS`, `FINISHED`, `ERROR`, `EXPIRED`, and published media ID separately. Reels use `media_type=REELS`; carousel children are marked `is_carousel_item` before the parent container.

The Provider fetches remote URLs, so the Skill cannot prove fetched bytes stayed immutable after prepare. It records the approved URL/host/expected MIME and refuses local files rather than adding a hosting service.

Official sources:

- [Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/)
- [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)
- [Instagram API with Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/)
- [Meta official Instagram Postman workspace](https://www.postman.com/meta/instagram/overview)
- [Official Postman content publishing collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)

Account eligibility, publishing quotas, permissions, media constraints, and container lifetime drift. Recheck official docs, app review, account state, and response at execution time.
