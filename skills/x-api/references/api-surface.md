# X API v2 surface

この文書はSkillが扱う最小のAPI面を定義する。料金、利用可能なプラン、Rate Limit、認証要件は変わり得るため、実行時は公式ドキュメントを再確認する。

## Endpoint map

| 操作 | Method | Path | 主な認証 |
| --- | --- | --- | --- |
| 自分のユーザー | GET | `/2/users/me` | User context |
| ユーザー検索 | GET | `/2/users/by/username/:username` | App-onlyまたはUser context |
| 投稿ID取得 | GET | `/2/tweets?ids=...` | App-onlyまたはUser context |
| ユーザーの投稿 | GET | `/2/users/:id/tweets` | App-onlyまたはUser context |
| 直近検索 | GET | `/2/tweets/search/recent` | App-onlyまたはUser context |
| 通常投稿 | POST | `/2/tweets` | User context |
| OAuth 2.0トークン更新 | POST | `/2/oauth2/token` | client credentials（Skill内部でのみ使用） |

既定のホストは `https://api.x.com` に固定する。`X_API_BASE_URL` による差し替えは、認証ヘッダーが指定先へ送られるため**loopback（localhost / 127.0.0.1 / ::1）のテストサーバーだけ**を受け付け、それ以外のホストはエラーで拒否する。差し替え使用時はstderrへ注記が出る。

## 認証

- App-only読み取り: `Authorization: Bearer $X_BEARER_TOKEN`
- User context（第一経路）: **OAuth 1.0a** HMAC-SHA1署名。`X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET` の4変数がすべて揃ったときに使う。Developer Portalで発行するAccess Token/Secretは失効しないため、長期運用エージェントはこちらを既定にする。
- User context（代替1・OAuth 2.0 refresh）: `X_OAUTH2_CLIENT_ID` と `X_OAUTH2_TOKEN_STORE`（confidential clientは `X_OAUTH2_CLIENT_SECRET` も、初回は `X_OAUTH2_REFRESH_TOKEN` も）を設定すると、Skillがアクセストークンを発行・キャッシュする。Xはリフレッシュトークンを使用のたびにローテーションするため、storeの読み取り、更新要求、新しい値の保存までを同じ排他lockで覆う。更新要求の直前には秘密値を含まない `<store>.refresh-pending` markerを0600で永続化し、storeへ新しいtokenと同じrotation IDを保存した後にmarkerを消す。プロセス停止後にmarkerだけが残った場合、同じrotation IDがstoreに無ければtoken状態を不明として自動更新を拒否し、再認可を要求する。
- `X_OAUTH2_TOKEN_STORE` は必須で、新しい値は0600の一時fileからatomic replaceし、fileと親directoryをfsyncする。更新応答が不明、応答にaccess tokenがない、または回転後のstore保存に失敗した場合は `credential_state=reauthorization_required` とmarker pathを返す。tokenやAuthorization headerを標準出力、stderr、marker、台帳へ出さない。
- User context（代替2・静的トークン）: OAuth 2.0 userトークン。`X_ACCESS_TOKEN` だけが設定されているとき `Bearer` で送る。既定で約2時間で失効するため、refresh基盤を持つ利用側だけが使う。
- OAuth 1.0aの4変数のうち一部だけが設定されている場合はエラーで停止する。OAuth 2.0へ黙って降格しない。
- OAuthのトークン発行、refresh、PKCEフローはこのSkillの責務ではない。既存の認証基盤を使い、トークンの保存・更新をこのSkillへ持ち込まない。
- OAuth 1.0aの署名対象はクエリパラメータとOAuthパラメータだけで、v2のJSONボディは署名に含めない。実装は公式ドキュメントの署名例（`docs.x.com`）とテストで照合済み。

## フィールドの既定値

スクリプトは取得量を抑えるため、操作ごとに必要なフィールドだけを指定する。追加フィールドが必要なときは、利用側の目的とコストを確認してスクリプトを拡張する。

- User: `created_at,description,location,public_metrics,profile_image_url,protected,url,verified`
- Post: `created_at,conversation_id,lang,possibly_sensitive,public_metrics`
- Search: `expansions=author_id` と上記Post/Userフィールド

## 公式情報

- [X Developer Platform](https://docs.x.com/overview)
- [X API tools: Get Posts](https://developer.x.com/apitools/api?endpoint=%2F2%2Fusers%2F%7Bid%7D%2Ftweets&method=get)
- [X API tools: Get Posts by IDs](https://developer.x.com/apitools/api?endpoint=%2F2%2Ftweets&method=get)
- [X API response codes and error support](https://developer.x.com/en/support/twitter-api/error-troubleshooting)
