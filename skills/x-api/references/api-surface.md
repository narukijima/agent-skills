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

既定のホストは `https://api.x.com`。テスト時だけ `X_API_BASE_URL` でローカルHTTPサーバーへ差し替えられる。

## 認証

- App-only読み取り: `Authorization: Bearer $X_BEARER_TOKEN`
- User context読み取り・投稿: `Authorization: Bearer $X_ACCESS_TOKEN`
- OAuthのトークン発行、refresh、PKCEフローはこのSkillの責務ではない。既存の認証基盤を使い、トークンの保存・更新をこのSkillへ持ち込まない。

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
