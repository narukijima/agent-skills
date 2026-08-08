# Posting safety contract

この文書は、X APIの通常投稿を実行する直前に読む。投稿は不可逆な外部作用であり、APIレスポンスが失われても公開済みの可能性がある。

## Required gates

すべて満たさない限り `--live` を実行しない。

1. 投稿本文が利用側のProjectで確定している。
2. 投稿アカウントとUser contextのアクセストークンの対応を確認している。
3. `X_POSTING_ENABLED=true` が実行環境で明示されている。
4. `--content-id` が利用側の台帳上で一意である。
5. `--ledger` が指定され、同じ本文とcontent_idの過去結果を検査できる。
6. `--dry-run` の出力を確認済みである。
7. 投稿後にIDを照合できる。結果不明を成功扱いしない。

## Ledger rules

台帳はJSON Linesで、本文そのものではなく `content_sha256` を重複判定の主キーにする。最低限、次を1回の試行ごとに記録する。

- `attempted_at`
- `content_sha256`
- `content_id`
- `status`: `sent`, `failed`, `unknown`
- `post_id`（取得できた場合）
- `http_status`（取得できた場合）

`sent` は同じ本文または同じ `content_id` を拒否する。`unknown` はAPI側の公開状態を照合するまで再送しない。再試行は最大2回までとし、利用側の運用台帳とも照合する。

## Do not do

- Authorizationヘッダーやトークンをログ・エラー・台帳へ保存しない。
- timeout後に盲目的に再送しない。
- XのWeb画面を投稿経路にしない。
- APIの料金やRate Limitを固定の成功条件にしない。
- このSkillの範囲へ返信、引用、いいね、フォロー、DM、削除を追加しない。
