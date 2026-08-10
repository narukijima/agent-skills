# X API v2 surface

このSkillが扱う最小API面を定義する。価格、plan、rate limit、field、制約は変わり得るため、実行時は公式docsとDeveloper Consoleを再確認する。

## Endpoint map

| Operation | Method | Path | Auth | Call budget |
| --- | --- | --- | --- | --- |
| authenticated identity | GET | `/2/users/me` | User context | 1 |
| username / user ID lookup | GET | `/2/users/by/username/:username`, `/2/users/:id` | App or User | 1 |
| posts by ID | GET | `/2/tweets?ids=...` | App or User | 1 |
| user posts | GET | `/2/users/:id/tweets` | App or User | 1 |
| recent search | GET | `/2/tweets/search/recent` | App or User | 1 |
| project usage | GET | `/2/usage/tweets` | selected read auth | 1 |
| text post workflow | POST/GET/POST | `/2/oauth2/token` (conditional), `/2/users/me`, `/2/tweets` | User context | maximum 3 |
| OAuth 2.0 refresh | POST | `/2/oauth2/token` | client authentication | user-context operationの予算へ含める |

readは`X_API_READ_ENABLED=true`と`X_API_READ_MAX_CALLS`、sendは`X_POSTING_ENABLED=true`とexact `X_API_WRITE_MAX_CALLS=3`を必須にする。App-only readは1 call、OAuth refreshの可能性がある通常のUser-context readは最大2 call、reconcileは最大3 callを事前予約する。両方で`X_API_PROJECT_ID`、`X_API_AGENT_ID`、kind別`X_API_DAILY_READ_CALL_LIMIT` / `X_API_DAILY_WRITE_CALL_LIMIT`をSQLiteへ予約し、累積上限を超えたcall planを外部request前に拒否する。`max_results`がAPI範囲外ならclampせず失敗させる。

これらの予算値とProject / Agent IDは、一般Agentが書き換えられないgateway-owned設定であることを前提にする。同じcallerが環境変数やusage databaseを変更できる構成では、ローカルcounterを強制的な課金上限とはみなさない。

## Authentication ownership

- App-only: `Authorization: Bearer $X_BEARER_TOKEN`
- OAuth 1.0a User context: `X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET`が全て揃った場合だけHMAC-SHA1署名を使う。通常expiryはないがrevoke、app停止、key再生成などで失効し得る。
- OAuth 2.0 User context refresh: `X_OAUTH2_CLIENT_ID`とprivate `X_OAUTH2_TOKEN_STORE`を使う。bootstrap時は`X_OAUTH2_REFRESH_TOKEN`、confidential clientは`X_OAUTH2_CLIENT_SECRET`も使う。
- Static OAuth 2.0 User token: `X_ACCESS_TOKEN`と、発行元public client IDの`X_OAUTH2_STATIC_CLIENT_ID`を使う。expiry / refreshを利用側が所有する。

OAuth 2.0はclient ID、OAuth 1.0aはAPI keyからcredential fingerprintを導出し、署名済みmanifestの`expected_app_fingerprint`と照合する。`X_API_APP_ID`はoperator向けlabelであり、このfingerprint照合の代替ではない。send / reconcileはuser credentialを一度だけ解決し、その同じsnapshotをidentity確認と後続requestへ使う。

OAuth 2.0 Authorization Code + PKCEのbrowser consent、callback、initial code exchangeはこのSkillの責務外である。一方、設定済みrefresh tokenのrotation、private store cache、atomic update、recovery markerはこのSkillの責務であり、文書上も区別する。

unattended refreshには少なくとも`tweet.read`、`tweet.write`、`users.read`、`offline.access`が必要になる。実際に要求するscopeはoperationへ最小化し、実行時の公式docsで確認する。`offline.access`なしではrefresh tokenを前提にしない。

## HTTP boundary

production baseは`https://api.x.com`に固定する。`X_API_BASE_URL` overrideは`X_API_TEST_MODE=true`かつ`X_POSTING_ENABLED`が無効な場合だけ、`localhost`、`127.0.0.1`、`::1`のtest serverを許す。全requestでredirectを拒否し、Authorization headerをredirect targetへ転送しない。

## Response envelope

readはprovider bodyを次へ正規化する。

```json
{
  "status": "success | partial | empty | failed",
  "data": {},
  "errors": [],
  "meta": {},
  "includes": {},
  "_meta": {
    "endpoint": "/2/...",
    "http_status": 200,
    "requested_at": "...",
    "auth_mode": "app | user",
    "rate_limit": {"limit": "...", "remaining": "...", "reset": "..."}
  }
}
```

2xxで`data`と`errors`が併存すれば`partial`、errorだけなら`failed`、dataが空なら`empty`とする。成功responseのrate-limit headerも返す。HTTP 429は`retry_after` / resetを返し、自動sleep / loopしない。

## OAuth 2.0 rotation safety

refresh request直前にsecretを含まない`<store>.refresh-pending`を0600 / fsyncでwrite-ahead保存する。storeは0600 temporary fileからatomic replaceし、file / parent directoryをfsyncする。response不明、5xx、access token欠落、rotated token保存失敗はcredential state unknownとしてmarkerを残し、自動refreshを止めてreauthorizationを要求する。4xx rejectionはrequestが確定拒否されたためmarkerをclearする。

## Official sources to recheck

- [X API overview](https://docs.x.com/overview)
- [Authenticated user lookup](https://docs.x.com/x-api/users/lookup/quickstart/authenticated-lookup)
- [Counting characters](https://docs.x.com/fundamentals/counting-characters)
- [Pricing](https://docs.x.com/x-api/getting-started/pricing)
- [Usage](https://docs.x.com/x-api/usage/introduction)
- [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits)
- [OAuth 1.0a access tokens](https://docs.x.com/fundamentals/authentication/oauth-1-0a/obtaining-user-access-tokens)
- [OAuth 2.0 Authorization Code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
