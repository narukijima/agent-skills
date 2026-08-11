---
name: x-api
description: Use when an agent must explicitly retrieve paid X API v2 data or prepare, send, or reconcile one text post through a manifest, expected-account binding, budgets, and canonical SQLite ledger.
license: MIT. See LICENSE.txt
metadata:
  claudagt.version: "0.5.1"
  claudagt.status: "active"
  claudagt.aliases: "x api,twitter-api"
---

# x-api — X API v2の明示的な読み取りとguarded text post

## 目的

このSkillはX API v2の限定された読み取りと、通常テキスト投稿の安全境界を提供する。投稿内容、文体、時刻、対象account、approvalは利用側Projectが所有し、このSkillはvalidation、認証、expected account照合、予算、重複防止、結果不明の回復を所有する。

対応範囲:

- read: authenticated user、user lookup、post lookup、user posts、recent search、usage
- write: 単独の通常テキスト投稿を `prepare` → `send` → 必要時 `reconcile` で扱う
- 対象外: reply、quote（本文中のX status URLによるquote cardも含む）、like、follow、DM、delete、media upload、browser posting、Analytics UI

write capabilityはbetaである。canonical SQLite ledgerは同一workspace内で、同じbundled scriptとdatabaseを使う複数processを直列化するが、任意コードを実行できる敵対的Agentや複数machineのglobal uniquenessは保証しない。複数Agent / machine / accountの完全無人運用は、署名鍵、資格情報、予算設定、bundled script、canonical databaseを一般Agentから隔離して所有する専用single-writer gateway経由でのみ採用する。

## 使用するKnowledge

### Required

- なし

### Conditional

- 条件: Endpoint、認証、課金、response contractを扱うとき
  参照: `references/api-surface.md`
- 条件: `prepare`、`send`、`reconcile`を扱うとき
  参照: `references/posting-safety.md`

価格、plan、rate limit、投稿制約は変わる。実行時は公式X docs / Developer Consoleを正本にし、Skill内の古い固定値で利用可否や費用を断定しない。

## Safety boundary

- Skillは明示的に呼び出す。paid readとexternal writeをimplicit invocationしない。
- secretは環境変数またはgateway-owned private token storeだけで受け取り、引数、manifest、台帳、stdout / stderrへ出さない。
- readは `X_API_READ_ENABLED=true`、`X_API_READ_MAX_CALLS=<n>`、Project / Agent別のdaily call limitを必須にする。範囲外の`max_results`は増量・clampせず拒否する。
- sendは `X_POSTING_ENABLED=true`、`X_API_WRITE_MAX_CALLS=3`、manifestと一致する`X_API_APP_ID`、OAuth app fingerprint、daily write limitを必須にする。3 callはOAuth 2.0 refreshの可能性、identity readback、post sendを覆う上限である。
- 認証付きHTTP redirectは全面拒否する。`X_API_BASE_URL` overrideはpostingを無効化した `X_API_TEST_MODE=true` のloopback test serverだけに限定する。
- `send`は`prepare`が生成した短期manifestだけを受け取る。direct `--text` / `--file` / `--ledger`は存在しない。
- manifestはgateway-owned `X_API_MANIFEST_SIGNING_KEY`によるHMAC-SHA256を必須にし、本文、account、app credential fingerprint、approval、期限、call planの再計算改ざんを拒否する。署名鍵を一般Agentへ渡してはならない。
- live前に`/2/users/me`を読み、manifestの`expected_user_id`とexact matchする。不一致時はSQLiteへattemptを書かない。
- ledgerは最寄りの`.git` markerから解決したworkspace rootの`state/x-api/x-posts.sqlite3`、daily usageは`state/x-api/x-usage.sqlite3`に固定し、caller-selected pathやvendorのdirectory深度を根拠にしない。markerを解決できなければfail closedにする。
- 同じaccountに未解決`unknown`が1件でもあれば、別contentの新規sendも停止する。
- timeout、disconnect、5xx、post ID欠落は`unknown`にする。blind retry optionは存在せず、`reconcile`が必要である。

## 認証

- app-only read: `X_BEARER_TOKEN`
- user context優先経路: OAuth 1.0aの4変数 `X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET`
- user context代替: `X_OAUTH2_CLIENT_ID` + `X_OAUTH2_TOKEN_STORE`。bootstrap時は`X_OAUTH2_REFRESH_TOKEN`、confidential clientは`X_OAUTH2_CLIENT_SECRET`も使う。
- pre-issued OAuth 2.0 user tokenは`X_ACCESS_TOKEN`と、そのtokenを発行したpublic client IDを示す`X_OAUTH2_STATIC_CLIENT_ID`で使う。期限と更新責任は利用側が持つ。

OAuth 1.0a tokenは通常expiryを持たないが、利用者のrevoke、app停止、key再生成などで無効になり得る。OAuth 2.0 refreshはSkillがprivate storeへatomic保存し、rotation結果不明時は自動再試行せずreauthorizationを要求する。詳細は`references/api-surface.md`を使う。

## Read workflow

1. 目的、Endpoint、fields、page、最大件数、call budgetを確定する。
2. 公式の現在価格とplan可用性を確認し、`X_API_READ_ENABLED=true`、invocation上限、daily上限、Project / Agent IDを設定する。
3. `scripts/x_api.py`を実行する。
4. envelopeの`status`を確認する。`partial`、`empty`、`failed`を`success`と同一視しない。
5. `_meta`のEndpoint、取得時刻、auth mode、rate limitと、paginationを証拠に残す。

```bash
X_API_READ_ENABLED=true X_API_READ_MAX_CALLS=1 \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_READ_CALL_LIMIT=100 \
  python3 skills/x-api/scripts/x_api.py --pretty user --username XDevelopers

X_API_READ_ENABLED=true X_API_READ_MAX_CALLS=1 \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_READ_CALL_LIMIT=100 \
  python3 skills/x-api/scripts/x_api.py --pretty usage
```

## Post workflow

### 1. Prepare

Projectで本文、stable X user ID、app label、OAuth app credential fingerprint、content ID、approval IDを確定し、approval gatewayで短期manifestを作る。prepareはNFC正規化、同一本文hash、weighted length、URL / cashtag、control characterを検査する。`x.com` / `twitter.com`のstatus URLは暗黙のquote targetとして分類し、`UNDECLARED_QUOTE_TARGET`で拒否する。X APIやX credentialは使わないが、一般Agentから隔離したmanifest署名鍵を必要とする。fingerprintはOAuth 2.0なら`sha256("oauth2:" + client_id)`、OAuth 1.0aなら`sha256("oauth1:" + api_key)`である。

```bash
X_API_MANIFEST_SIGNING_KEY='<gateway-owned-32-byte-minimum-secret>' \
python3 skills/x-api/scripts/x_api.py --pretty prepare \
  --manifest .tmp/approved-post.json \
  --content-id 2026-08-10-001 \
  --expected-user-id 123456789 \
  --app-id x-production \
  --expected-app-fingerprint '<64-char-sha256>' \
  --approval-id approval-2026-08-10-001 \
  --text '確定済み本文'
```

### 2. Send

manifestの本文、account、hash、app fingerprint、approval、期限、3-call上限planを変更せず送る。sendはidentity mismatch、credential app mismatch、expired / unsigned / tampered manifest、duplicate、unresolved unknown、attempt上限、budget不足をexternal write前に拒否する。OAuth credentialは一度だけ解決し、identity readbackとPOSTで同じsnapshotを使う。

```bash
X_POSTING_ENABLED=true X_API_WRITE_MAX_CALLS=3 X_API_APP_ID=x-production \
  X_API_MANIFEST_SIGNING_KEY='<gateway-owned-32-byte-minimum-secret>' \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_WRITE_CALL_LIMIT=20 \
  python3 skills/x-api/scripts/x_api.py --pretty send \
  --manifest .tmp/approved-post.json
```

### 3. Reconcile

結果が`unknown`なら再送せず、同じaccountのrecent postsとcanonical hash / attempt timeを照合する。見つかれば`confirmed_success`、取得windowがattempt時刻を覆って不在なら`confirmed_absent`、証明できなければ`unresolved`のまま止める。

```bash
X_API_READ_ENABLED=true X_API_READ_MAX_CALLS=3 \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_READ_CALL_LIMIT=100 \
  python3 skills/x-api/scripts/x_api.py --pretty reconcile \
  --content-id 2026-08-10-001 \
  --expected-user-id 123456789
```

## 出力契約

- readは`status`、`data`、`errors`、provider `meta / includes`、request `_meta`を返す。
- prepareはnormalized text、validation、manifest hash / HMAC、account / credential app / approval binding、expiry、call planを返す。
- prepare / sendはmarkerから解決したworkspace rootと解決根拠を返す。send成功はaccount ID、app ID、content ID、post ID / URL、content hash、canonical ledger path、rate metadataも返す。
- failureはsecretを含まないstructured errorをstderrへ返してnon-zero exitする。
- `unknown`、`partial`、`empty`、`rate_limited`、`confirmed_absent`、`unresolved`を意味どおり区別する。

## 決定的な実行コード

通常は`scripts/x_api.py`だけを使う。transportを公式xurl / XDKへ置き換える場合も、manifest-only send、expected-account binding、canonical single writer、budget、redirect拒否、unknown reconciliationを上位policyとして維持する。XMCPなど広いTool surfaceを使う場合は対象operationだけをallowlistする。

## 禁止事項

- `send`をraw text、任意file、任意ledger pathで呼べるinterfaceへ戻さない。
- expected user IDをusername、display name、credential存在で代用しない。
- `unknown`をflag一つでretry可能にしない。
- local SQLiteを複数machineのdistributed lockと主張しない。
- tokenを一般Agentのprompt、log、manifest、databaseへ保存しない。
- response bodyだけを返してrate limit、partial errors、request metadataを捨てない。
- scope拡張をminor implementation detailとして行わない。
