# Instagram Provider

## Contract

- Pinned version: `v26.0`.
- Hosts/auth modes: `graph.facebook.com` with Facebook Login, or `graph.instagram.com` with Instagram Login. Set `SNS_INSTAGRAM_AUTH_MODE` explicitly; never auto-collapse the modes.
- Accounts: Instagram Professional Business/Creator only.
- Facebook Login permissions for this surface commonly include `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`, and Page discovery/assignment permissions such as `pages_show_list` when needed.
- Instagram Login uses its separate permissions, including `instagram_business_basic` and `instagram_business_content_publish` for this surface.

Reads cover Professional identity and owned media. Publishing covers remote JPEG image, MP4/MOV video or Reel, and 2–10 item carousel. Every publish creates Provider containers, checks `status_code`, and calls `media_publish` only when ready. Preserve `IN_PROGRESS`, `FINISHED`, `ERROR`, `EXPIRED`, container ID, and published media ID separately. Reels use `media_type=REELS`; carousel children are marked `is_carousel_item` before the parent container.

Persist the state machine `creating_children → creating_parent` (or `creating_container`) `→ container_created → processing → ready → final_publish_started → published`. Reuse checkpointed child IDs on resume. If a process dies or times out before a container response is checkpointed, reconciliation may convert the intent to resumable `submitted` after a grace window because no public publish call started; recreating an orphan container is allowed. Once `media_publish` starts, never repeat it blindly. Reconcile a lost final response through a unique owned-media match using signed caption, native media type, and attempt window.

Prepare rejects non-JPEG images, non-MP4/MOV video, invalid declared size/container, and any declared codec/audio/fps outside H.264/HEVC, AAC, and 23–60 fps. Metadata that cannot be inspected from a remote URL remains Provider-side validation; omission is not a claim of conformance. The current implementation caps declared image size at 8 MB and video at 1 GB; recheck official docs at execution time.

The Provider fetches remote URLs, so the Skill cannot prove fetched bytes stayed immutable after prepare. It records the approved URL/host/expected MIME and optional size/container/codec/fps metadata and refuses local files rather than adding a hosting service.

Official sources:

- [Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/)
- [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)
- [Instagram API with Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/)
- [Meta official Instagram Postman workspace](https://www.postman.com/meta/instagram/overview)
- [Official Postman content publishing collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)

Account eligibility, publishing quotas, permissions, media constraints, and container lifetime drift. Recheck official docs, app review, account state, and response at execution time.
