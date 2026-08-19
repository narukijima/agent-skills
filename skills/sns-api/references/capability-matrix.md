# Capability matrix

The runtime registry is authoritative; inspect it with `capabilities`. This document explains the contract and is tested against the registry.

| Platform | Status | Read | Publish | Status/recovery |
| --- | --- | --- | --- | --- |
| X | supported | `identity.read`, `user.lookup`, `post.lookup`, `user.posts`, `post.search.recent`, `usage.read` | `publish.text`, `publish.quote`, `publish.image`, `publish.video`, `publish.gif`, `media.upload.chunked` | `publish.status`, `reconcile`, `manual.resolve` |
| YouTube | supported | `identity.read`, `video.lookup`, `own.videos` | `publish.video`, `media.upload.resumable` | `publish.status`, `reconcile` |
| Facebook | supported | `identity.read`, `page.content` | `publish.text`, `publish.image`, `publish.video` | `publish.status`, `reconcile`, `manual.resolve` |
| Instagram | supported | `identity.read`, `media.read`, `publishing.limit` | `publish.image`, `publish.video`, `publish.reel`, `publish.carousel` | `publish.status`, `reconcile` |
| Threads | supported | `identity.read`, `own.posts`, `publishing.limit` | `publish.text`, `publish.image`, `publish.video`, `publish.carousel` | `publish.status`, `reconcile` |
| TikTok | planned | planned only | planned only | runtime unsupported |

Scope exclusions are deliberate: DM, ads, arbitrary comment moderation, follow/unfollow, like/unlike, arbitrary delete, full analytics/insights, browser automation, unofficial/private APIs, storage hosting, content/caption generation, scheduling, and strategy.

Facebook means Pages only. Instagram means Professional accounts only. A missing capability is a hard refusal; it is not permission to call another endpoint directly.
