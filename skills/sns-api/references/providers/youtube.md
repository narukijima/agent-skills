# YouTube Provider

## Contract

- Hosts: `www.googleapis.com` and Provider-returned resumable sessions on the same allowlist.
- API: YouTube Data API v3.
- Auth: pre-provisioned OAuth 2.0 user access token. Use `youtube.readonly` for reads where sufficient and `youtube.upload` for upload; request broader scopes only when an operation truly requires them.
- Account binding: the singular channel returned by `channels.list(part=id,snippet,contentDetails,mine=true)`.

Reads cover authenticated channel identity, video lookup, the channel uploads playlist, and upload/processing status. `publish.video` accepts exactly one verified local video plus title, description, tags, category, privacy status, made-for-kids declaration, and altered/synthetic-content declaration.

The adapter initiates `videos.insert` with `uploadType=resumable`. Its provider-owned transport authenticates every session PUT with the current Bearer token, uses bounded sequential ranges, and on interruption sends an empty authenticated PUT with `Content-Range: bytes */TOTAL`; a `308 Range` determines the exact next byte. Do not start a new session while the private one is recoverable.

Treat the returned session URI as a capability secret. Store it only in canonical owner-controlled private state (`state/sns-api/private/youtube-upload-sessions/`, directory 0700/files 0600), bound to platform/account/intent/asset hash/size/MIME. SQLite receives only a random opaque handle, URI SHA-256, byte offset, and non-secret status. The URL never enters manifest, audit detail, stdout, or stderr.

A returned video ID means `submitted` unless `processingDetails.processingStatus` proves success. Poll `videos.list(part=status,processingDetails,id=...)`; preserve `processing`, `succeeded`, `failed`, and `terminated` distinctly. If the approval expires while upload/processing is submitted, issue `authorize-resume` with a new approval bound to the unchanged Provider state.

Unverified API projects may have uploads restricted to private. Quota cost, daily quota, upload restrictions, and audit state drift; recheck the Cloud Console, official docs, and response at execution time.

Official sources:

- [Channels.list](https://developers.google.com/youtube/v3/docs/channels/list)
- [Videos.list](https://developers.google.com/youtube/v3/docs/videos/list)
- [Videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [Video processing status](https://developers.google.com/youtube/v3/guides/implementation/videos#check-the-status-of-an-uploaded-video)
- [OAuth 2.0 scopes](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps)
