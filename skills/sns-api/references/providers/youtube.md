# YouTube Provider

## Contract

- Hosts: `www.googleapis.com` and Provider-returned resumable sessions on the same allowlist.
- API: YouTube Data API v3.
- Auth: pre-provisioned OAuth 2.0 user access token. Use `youtube.readonly` for reads where sufficient and `youtube.upload` for upload; request broader scopes only when an operation truly requires them.
- Account binding: the singular channel returned by `channels.list(part=id,snippet,contentDetails,mine=true)`.

Reads cover authenticated channel identity, video lookup, the channel uploads playlist, and upload/processing status. `publish.video` accepts exactly one verified local video plus title, description, tags, category, privacy status, made-for-kids declaration, and altered/synthetic-content declaration.

The adapter initiates `videos.insert` with `uploadType=resumable` and streams the verified file with `Content-Range`. Treat the returned session URI as capability-sensitive: the canonical ledger stores only its SHA-256 checkpoint, not the URI. A returned video ID means `submitted` unless `processingDetails.processingStatus` proves success. Poll `videos.list(part=status,processingDetails,id=...)`; preserve `processing`, `succeeded`, `failed`, and `terminated` distinctly.

Unverified API projects may have uploads restricted to private. Quota cost, daily quota, upload restrictions, and audit state drift; recheck the Cloud Console, official docs, and response at execution time.

Official sources:

- [Channels.list](https://developers.google.com/youtube/v3/docs/channels/list)
- [Videos.list](https://developers.google.com/youtube/v3/docs/videos/list)
- [Videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [Video processing status](https://developers.google.com/youtube/v3/guides/implementation/videos#check-the-status-of-an-uploaded-video)
- [OAuth 2.0 scopes](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps)
