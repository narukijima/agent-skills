# Facebook Provider

## Contract

- Host/version: `https://graph.facebook.com/v26.0`; version is deliberately pinned and updated only with tests/docs review.
- Auth: Page Access Token tied to the configured app and a person/system user authorized to manage the Page.
- Permissions/tasks: minimize to the current Pages requirements for the selected operation, commonly `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`, plus the Page's content-creation task. Confirm app review and Page task at execution time.
- Account binding: `GET /v26.0/{page-id}` must return the exact stable Page ID.

Reads cover Page identity and Page feed/content. Writes cover Page text (`/{page-id}/feed`), remote image (`/{page-id}/photos`), and remote video (`/{page-id}/videos`) publishing. Personal-profile posting is intentionally absent. The Skill does not implement Groups, ads, Stories, comments, deletion, or Messenger.

Checkpoint `publish_started` and its timestamp before the Page write. Remote media remains mutable after prepare. Facebook video acceptance is `submitted`; use the status surface and native status object before calling it fully published. If a response is lost before an object ID is known, reconcile against a unique recent Page-content item using the signed message/caption/description and attempt window. Feed absence is not proof of absence. A crash durably known to precede the write checkpoint can become `confirmed_absent` after a grace window. Otherwise, if official evidence remains inconclusive, Facebook Pages supports the same signing-key-gated, reasoned, provider-ID-validated `manual.resolve` audit path as X; never edit the ledger directly.

Official sources:

- [Pages API overview](https://developers.facebook.com/docs/pages-api/)
- [Pages API getting started](https://developers.facebook.com/docs/pages-api/getting-started/)
- [Page feed reference](https://developers.facebook.com/docs/graph-api/reference/page/feed/)
- [Page photos reference](https://developers.facebook.com/docs/graph-api/reference/page/photos/)
- [Page videos reference](https://developers.facebook.com/docs/graph-api/reference/page/videos/)
- [Meta official Facebook Postman workspace](https://www.postman.com/meta/facebook/overview)
- [Graph API versioning](https://developers.facebook.com/docs/graph-api/overview/versioning/)

Permissions, Page tasks, media restrictions, and version behavior drift. Recheck official docs, App Dashboard, and response headers before live use.
