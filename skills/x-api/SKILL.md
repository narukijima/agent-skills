---
name: x-api
description: Use when an agent must retrieve X API v2 data or prepare or send a guarded post through the official REST API, with explicit authentication, cost, duplicate, and live-send checks.
status: active
aliases: [x api, twitter-api]
version: 0.4.1
---

# x-api — X API v2 の取得と安全な投稿

## 目的

このSkillは、X API v2を使う汎用的な読み取りと通常投稿を提供する。投稿の内容・時刻・文体・題材選定は利用側のProjectが所有し、このSkillはXへの境界だけを所有する。

対応する操作は次のとおり。

- 読み取り: 自分のユーザー情報、ユーザー検索、投稿取得、ユーザーの投稿、直近検索
- 書き込み: 通常投稿（単独のテキスト投稿）
- 対象外: 返信、引用、いいね、フォロー、DM、削除、画像・動画アップロード、ブラウザ操作、Analytics画面の読み取り

対象外は未実装ではなく設計上の境界である（詳細は `references/posting-safety.md` の "Do not do"）。範囲を広げる場合は契約変更としてversionを上げて行う。

## 使用するKnowledge

### Required

- なし

### Conditional

- 条件: X APIのEndpoint、認証、料金の確認が必要なとき
  参照: `references/api-surface.md`
- 条件: 通常投稿を実行するとき
  参照: `references/posting-safety.md`

## 最初に読むもの

- X APIのEndpoint、認証、料金の確認が必要なときは `references/api-surface.md` を読む。
- 投稿を実行するときは `references/posting-safety.md` を読む。

## 認証と費用の扱い

- 秘密値は環境変数だけで受け取る。トークンをコマンド引数、原稿、ログ、Gitへ書かない。
- 公開データの読み取りは `X_BEARER_TOKEN` を使う。
- ユーザーコンテキストの第一経路は**OAuth 1.0a**（`X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET` の4変数、トークンは失効しない）。OAuth 2.0運用の利用側は `X_OAUTH2_CLIENT_ID` + `X_OAUTH2_TOKEN_STORE` でSkillにrefreshを任せられる（ローテーションされたトークンは指定ファイルへ0600で保存、出力へは出さない）。`X_ACCESS_TOKEN` だけの静的トークンも使える（約2時間で失効）。詳細は `references/api-surface.md`。
- `me` と投稿はユーザーコンテキストを必須とする。`X_BEARER_TOKEN`だけで代用しない。
- APIの料金、利用可能なEndpoint、Rate Limitは実行時の公式情報を優先し、Skill内に固定値を作らない。

## 読み取りワークフロー

1. 必要な情報を最小のEndpointとフィールドへ絞る。全件取得や不要なexpansionを既定にしない。
2. `scripts/x_api.py` を使い、結果はJSONとして保存または次の処理へ渡す。
3. HTTPエラー、429、空結果を成功扱いしない。レスポンスの `data`、`errors`、ページネーションを確認する。
4. 取得結果に基づく主張を作る場合は、取得時刻、Endpoint、検索条件、ページネーションの有無を記録する。

例:

```bash
python3 skills/x-api/scripts/x_api.py --pretty me
python3 skills/x-api/scripts/x_api.py --pretty user --username XDevelopers
python3 skills/x-api/scripts/x_api.py --pretty posts --user-id 2244994945 --max-results 10
python3 skills/x-api/scripts/x_api.py --pretty search-recent --query 'from:XDevelopers -is:retweet' --max-results 10
```

## 投稿ワークフロー

投稿は常に次の順序で扱う。

1. 利用側のProjectが本文、宛先アカウント、投稿目的、送信許可を確定する。
2. まず `--dry-run` で本文のSHA-256、文字数、重み付き文字数（X基準の推定値。全角・絵文字は2、URLは23換算、上限280）を確認する。dry-runは既定動作である。
3. 実送信が明示された場合だけ、`--live`、`X_POSTING_ENABLED=true`、`--content-id`、`--ledger <path>` の4条件を確認する。
4. 台帳に同じ `content_id` または本文の `content_sha256` の `sent` があれば拒否する。ネットワーク結果が `unknown` の本文は、自動再送しない。再試行は利用者が `--retry-unknown` を明示した場合だけ許可し、試行上限は2回である。
5. 台帳は自動で2行書かれる。送信直前に `attempt` 行（`unknown`）、結果判明後に `result` 行。送信中にプロセスが落ちても `attempt` 行が残り、次回は `--retry-unknown` のゲートにかかる。結果不明のときは `unknown` のまま残す。

例:

```bash
python3 skills/x-api/scripts/x_api.py post --text 'ここに確定済みの投稿本文'
python3 skills/x-api/scripts/x_api.py post --live \
  --content-id 2026-08-08-20-01 \
  --text 'ここに確定済みの投稿本文' \
  --ledger .tmp/x-post-ledger.jsonl
```

`--live` はAPI認証、権限、課金、公開結果を伴う外部作用である。本文が未確定、台帳がない、投稿後の照合方法がない、または利用側の送信許可が不明な場合は実行せず、dry-run結果を返す。

## 出力契約

- 成功した読み取りはAPIのJSONを標準出力へ返す。
- dry-runは送信しないことが分かるJSONを返す。
- 成功した投稿は投稿ID、URL、本文SHA-256、台帳パスを返す。トークンやAuthorizationヘッダーは返さない。
- 失敗は標準エラーへ短い原因を返し、非0終了する。429では `retry_after` / `rate_limit_reset` をエラー出力へ含め、待機の判断材料を利用側へ返す。429は試行予算を消費しない。自動ループしない。

## 決定的な実行コード

通常は `scripts/x_api.py` を使う。別の言語やSDKへ置き換える場合も、dry-run既定、liveゲート、重複拒否、unknown再送拒否、秘密値非表示の契約を維持する。
