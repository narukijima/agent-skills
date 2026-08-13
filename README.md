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

X / YouTube / Facebook Pages / Instagram Professional / Threadsの公式APIを、Common Safety Core + Provider Adapterで扱います。署名manifest、stable account/app/credential binding、Project/Agent call budget、canonical SQLite、media hash、write-ahead unknown、duplicate防止、Provider固有status/reconcileを共通化します。TikTokはplannedで、runtime未対応です。

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

`send` はmanifest以外のplatform/text/media/account/ledger overrideを受け取りません。SQLiteは同一workspace/hostのcooperating processを直列化しますが、複数machineのglobal uniquenessは保証しません。完全無人のmulti-machine運用はdedicated single-writer gateway/central stateを使います。

Migration: 旧canonical `x-api` は `sns-api` にsupersedeされました。`x-api` / `x api` / `twitter-api` はactivation migration aliasとして残し、旧 `X_*` environmentは新 `SNS_*` と同値の場合だけ互換読取します。canonical implementation、path、state、CI、evalは `sns-api` 一つです。旧ledgerは自動変換せず、未解決intentを旧workflowで解消してから新しい`state/sns-api/`を開始します。

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
