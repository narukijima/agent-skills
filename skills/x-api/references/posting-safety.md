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

台帳はJSON Linesで、本文そのものではなく `content_sha256` を重複判定の主キーにする。1回の試行は**2行**で記録する（write-ahead方式）。

1. `event: "attempt"` — API呼び出しの**直前**に `status: "unknown"` で追記する。送信中にプロセスが落ちてもこの行が残り、次回実行は `--retry-unknown` のゲートにかかる。
2. `event: "result"` — 結果が判明した後に確定値で追記する。

各行は最低限、次を含む。

- `attempted_at`
- `event`: `attempt`, `result`（無い行は旧形式で、1行=1試行として数える）
- `content_sha256`
- `content_id`
- `status`: `sent`, `failed`, `unknown`, `rate_limited`
- `post_id`（取得できた場合）
- `http_status`（取得できた場合）
- `retry_after` / `rate_limit_reset`（429の場合）

現在の状態は同一 `content_id` / `content_sha256` の**最新行**で判定する。試行回数は `attempt` 行（と旧形式の行）の数で数え、上限は2回のまま変わらない。ただし `rate_limited` の `result` 行は対応する試行を**返還**する — 429はサーバーがリクエストを処理していないことが確実で、時間をおけば成功しうるため、不可逆事故に備えた試行予算を消費しない（旧形式では `http_status: 429` の行を数えない）。

重複検査からattempt行の追記までは台帳ファイルの排他ロック（`<ledger>.lock` への `flock`）の下で行い、並行実行の片方だけが送信できる。ロックが使えないプラットフォームではstderrへ警告を出す。

`sent` は同じ本文または同じ `content_id` を拒否する。`unknown` はAPI側の公開状態を照合するまで再送しない。再試行は最大2回までとし、利用側の運用台帳とも照合する。

`failed` と `unknown` の境界は「サーバーがリクエストを処理した可能性があるか」で引く。

- `unknown`: タイムアウト、ネットワーク断、HTTP 5xx。レスポンスが失われただけで投稿は公開済みの可能性がある。
- `failed`: HTTP 4xx（認証、権限、重複、レート制限）。処理前に拒否されたことが確実なので、上限内での再試行を許す。

## Do not do

- Authorizationヘッダーやトークンをログ・エラー・台帳へ保存しない。
- timeout後に盲目的に再送しない。
- XのWeb画面を投稿経路にしない。
- APIの料金やRate Limitを固定の成功条件にしない。
- このSkillの範囲へ返信、引用、いいね、フォロー、DM、削除を追加しない。
- 画像・動画アップロードは0.x系では**設計上の境界**として範囲外（未実装ではない）。必要な利用側が独自経路を持つ場合も、台帳・重複拒否・unknown再送拒否と同等の防御を必ず備える。将来対応する場合は契約変更としてminor versionを上げて行う。
