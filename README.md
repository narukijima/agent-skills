# agent-skills

`agent-directory` で構築した複数Agentが共通利用するSkillの公式配布元です。Skillは必要なものだけを明示的にvendored copyとして導入します。

- 利用側: [`claudagt/agent-directory`](https://github.com/claudagt/agent-directory)
- 配布元: [`claudagt/agent-skills`](https://github.com/claudagt/agent-skills)

## 構成

```text
skills/<skill-name>/
├── SKILL.md                 # 発動契約と入口
├── LICENSE.txt
├── agents/openai.yaml       # UI metadata
├── references/              # 条件付きで読む詳細
└── scripts/                 # 決定的な実行処理
```

`SKILL.md` は短い入口、詳細仕様は `references/`、再実装を避ける固定処理は `scripts/` に置きます。

## 責務境界

このRepositoryの責務は `Reusable Capability + Domain-specific safety` です。Codex / Claude Codeのshell、filesystem、network、sandbox、provider execution modeを設定・判定せず、Generic Runtime PermissionやAgent ACLを所有しません。Runtimeが操作を実行できない場合はRuntime層のerrorとして扱います。

Skill固有の安全境界は維持します。例えば`seo`の診断・変更scope、`ai-native-design`が設計対象productへ求めるserver-side authorization、`sns-api`のaccount/content/credential binding、署名manifest、budget、duplicate防止、unknown reconciliationはDomain Safetyです。これらをRuntimeの実行許可の代替にはしません。

## 導入

```bash
bash tools/import-skill.sh sns-api --target /path/to/agent-directory
```

自動import・fleet-wide自動同期はしません。既存の同名Skillを上書きせず、copy元repository、commit SHA、version、frontmatter projection、import時刻を `skills/sns-api/agents/upstream.yaml` に記録します。更新は利用側で差分を確認してから明示的に再importします。

## 公式Skill

### ai-native-design

既存design systemを調査し、AI固有state、untrusted generated content、server-side approval、accessibility、responsive、検証証拠まで扱います。

### seo

対象Project、Search Console、HTTP/rendered output、crawler logを優先し、`Observe → Measure → Diagnose → Fix → Verify`で検索・AI可視性を扱います。

### sns-api

X / YouTube / Facebook Pages / Instagram Professional / Threadsの公式APIを、Common Safety Core + Provider Adapterで扱います。署名manifest、stable account/app/credential binding、Domain Authorization、Project/Agent call budget、canonical SQLite、media hash、write-ahead unknown、duplicate防止、Provider固有status/reconcileを共通化します。XはURL引用・画像・動画・GIF、YouTubeは認証付きresumable upload、Instagram/Threadsはstage-aware container recoveryを扱います。TikTokはplannedで、runtime未対応です。

`approval-id` は互換CLI名です。意味は「人間がshell実行を許可した証拠」ではなく、上位Projectで外部作用intentがauthorizedであることを示すopaqueなDomain Authorization referenceです。同じaccount/content/operationのdefinite failure retryやstate-bound resumeでは同じreferenceを再利用でき、追加Human Approvalを要求しません。account、content hash、credential、operation等が変われば別intentとして再検証します。

自動投稿等ではProject-owned signed standing authorizationを`--standing-authorization-file`で渡せます。これはplatform、account/type、app、operations、credential fingerprint、allowed content sources、1 intentあたりと1日あたりのcall上限、Project/Agent caller、schedule、期間を固定します。HMACと全条件が一致する場合だけ短命manifestを作り、毎回のHuman Approvalを増やしません。standing authorizationはshell/network実行権限ではなく、`send`が受け取るのも引き続き署名済みmanifestだけです。

```bash
python3 skills/sns-api/scripts/sns_api.py capabilities
python3 skills/sns-api/scripts/sns_api.py capabilities --platform x

SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform x --operation publish.text \
  --payload '{"text":"approved text"}' \
  --manifest .tmp/approved-x.json \
  --content-id c-1 --expected-account-id 123456789 \
  --account-type user --app-id x-production \
  --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-1

SNS_API_WRITE_ENABLED=true SNS_API_WRITE_MAX_CALLS=3 \
SNS_API_PROJECT_ID=project-1 SNS_API_AGENT_ID=agent-1 \
SNS_API_DAILY_WRITE_CALL_LIMIT=20 \
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py send \
  --manifest .tmp/approved-x.json
```

Xの引用はreplyや自動コメントではなく、`publish.quote` の `quote_url` を署名済み本文へ正規化して含めます。画像は1–4個のlocal asset、動画はlocal MP4、GIFはlocal GIFを指定します。以下の画像例ではlocal path/MIME/size/SHA-256とalt textがmanifestへ固定され、upload後にもasset hashを再検証してから `media_ids` 付きPostを作成します。

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform x --operation publish.quote \
  --payload '{"text":"approved comment","quote_url":"https://x.com/example/status/123456789"}' \
  --manifest .tmp/approved-x-quote.json \
  --content-id c-quote-1 --expected-account-id 123456789 \
  --account-type user --app-id x-production \
  --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-quote-1
```

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare \
  --platform x --operation publish.image \
  --payload '{"text":"approved caption","assets":[{"kind":"local","path":"/absolute/photo.png","mime":"image/png"}],"alt_texts":["Approved image description"]}' \
  --manifest .tmp/approved-x-image.json \
  --content-id c-image-1 --expected-account-id 123456789 \
  --account-type user --app-id x-production \
  --expected-credential-fingerprint '<sha256>' \
  --approval-id approval-image-1
```

`send` はmanifest以外のplatform/text/media/account/ledger overrideを受け取りません。X mediaの正確な `SNS_API_WRITE_MAX_CALLS` はasset数・chunk数・alt textからmanifestの `provider_call_plan.max_calls` に固定されます。SQLiteは同一workspace/hostのcooperating processを直列化しますが、複数machineのglobal uniquenessは保証しません。完全無人のmulti-machine運用はdedicated single-writer gateway/central stateを使います。

YouTubeのsession URLはSQLiteや出力へ保存せず、canonical workspaceの0600 private stateへ保持します。Instagram/Threadsのcontainer作成中断はreconcile後に同じintentとして再開し、final publish開始後の不明状態とは分離します。`submitted`中にmanifestが期限切れになった場合は、payloadと現provider stateを変えず、同じDomain Authorization referenceで新しい短命resume manifestを発行します。

```bash
SNS_API_MANIFEST_SIGNING_KEY='<gateway-owned-secret>' \
python3 skills/sns-api/scripts/sns_api.py prepare-resume \
  --manifest .tmp/expired-submitted.json \
  --resume-manifest .tmp/approved-resume.json
```

Migration: 旧canonical `x-api` は `sns-api` にsupersedeされました。`x-api` / `x api` / `twitter-api` はactivation migration aliasとして残し、旧 `X_*` environmentは新 `SNS_*` と同値の場合だけ互換読取します。canonical implementation、path、state、CI、evalは `sns-api` 一つです。旧runtimeを停止後、`python3 skills/sns-api/scripts/sns_api.py migrate-legacy-x` でcanonical `state/x-api/` の投稿・unknown・重複・usage安全状態を監査付きで移行してください。最初のX write/recoveryと各budgeted X callも対応するguardを自動実行し、旧stateが不正または移行後に変更された場合はfail closedにします。

## 検証

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests -v
python3 tools/score-behavior-eval.py --cases evals/ai-native-design/cases.json
python3 tools/score-behavior-eval.py --cases evals/seo/cases.json
python3 tools/score-behavior-eval.py --cases evals/sns-api/cases.json
```

CIではcompile、全unit test、behavior eval schema、secret scanも実行します。score commandは定義検証では `behavior_run: false` を返します。実behavior評価は各promptを独立Agentで実行し、semantic judgmentと証拠を `--judgments` で採点します。

このRepositoryは投稿内容、文体、時刻、account固有人格、caption生成、戦略、schedulerを所有しません。利用側Projectが意思決定し、Skillは公式APIを安全に実行します。
