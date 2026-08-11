# agent-skills

`agent-directory` で作った複数のAgentが共有できるSkillの公式配布元です。SkillはAgent Workspaceへ直接組み込まず、必要なSkillのディレクトリだけを導入します。

- 利用側: [`claudagt/agent-directory`](https://github.com/claudagt/agent-directory)
- 配布元: [`claudagt/agent-skills`](https://github.com/claudagt/agent-skills)

## 構成

```text
skills/<skill-name>/
├── SKILL.md                 # 発動条件、手順、出力契約
├── agents/openai.yaml       # UI表示情報
├── references/              # 必要時に読む詳細仕様
└── scripts/                 # 決定的な実行コード
```

`SKILL.md` は短い入口、`references/` は条件付き詳細、`scripts/` は再実装を防ぐ固定処理です。利用側はリポジトリ全体ではなく、必要なSkillだけをインポートします。

## 導入

```bash
bash tools/import-skill.sh x-api --target /path/to/agent-directory
```

初期状態で自動インポート・自動同期は行いません。必要になったAgentの作業時だけ、利用側リポジトリのrootを指定して実行します。

canonical SkillはAgent Skills仕様に従い、version / status / aliasesを`metadata.claudagt.*`に置きます。インポート時だけ、agent-directory v1の検索・検証契約に必要なtop-level `status` / `aliases`を同じmetadataから決定的に投影します。それ以外はvendored copyで、既存の同名Skillは自動上書きしません。コピー元のrepository、commit SHA、`metadata.claudagt.version`、frontmatter projection、インポート時刻を `skills/x-api/agents/upstream.yaml` に記録します。Skill内の `LICENSE.txt` とthird-party noticeもコピー対象です。更新は利用側で差分を確認してから、明示的に再インポートします。

## 公式Skill

### ai-native-design

既存design systemを先に調査し、一般UIは既存component / shadcn/ui、AI-native UIはVercel AI Elements、tool-heavy UIは21st Agent Elements、一般の21st.dev Marketplaceは規約に沿ったdesign discoveryとして比較します。AI固有state、untrusted generated content、server-side approval、accessibility、responsive、検証証拠までを一つの実行protocolで扱います。

### x-api

X API v2の明示予算付きreadと、manifest / expected account / canonical SQLite ledgerでguardした通常テキスト投稿です。本文中のX status URLもquoteとして既定拒否し、ledger rootは`.git` markerから解決します。reply・quote・like・follow・DM・delete・media・browser操作は含めません。write capabilityは同一workspaceのsingle-writer betaで、複数machineの完全無人運用には専用gatewayが必要です。

```bash
X_API_READ_ENABLED=true X_API_READ_MAX_CALLS=1 \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_READ_CALL_LIMIT=100 \
  python3 skills/x-api/scripts/x_api.py --pretty user --username XDevelopers

X_API_MANIFEST_SIGNING_KEY='<gateway-owned-32-byte-minimum-secret>' \
python3 skills/x-api/scripts/x_api.py --pretty prepare \
  --manifest .tmp/approved-post.json --content-id c-1 \
  --expected-user-id 123456789 --app-id x-production \
  --expected-app-fingerprint '<64-char-sha256>' \
  --approval-id approval-1 --text '確定済み本文'

X_POSTING_ENABLED=true X_API_WRITE_MAX_CALLS=3 X_API_APP_ID=x-production \
  X_API_MANIFEST_SIGNING_KEY='<gateway-owned-32-byte-minimum-secret>' \
  X_API_PROJECT_ID=project-1 X_API_AGENT_ID=agent-1 X_API_DAILY_WRITE_CALL_LIMIT=20 \
  python3 skills/x-api/scripts/x_api.py --pretty send \
  --manifest .tmp/approved-post.json
```

## 検証

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests -v
python3 tools/score-behavior-eval.py --cases evals/ai-native-design/cases.json
```

CIでは上記に加えて、公式 `skills-ref` validatorを全Skillへ実行します。
最後のcommandはeval定義だけを検証し、`behavior_run: false` を返します。実behaviorをpassにするには、各promptを独立Agentで実行し、semantic criterionごとの判定と証拠を `--judgments` で採点します。keyword出現だけではpassにしません。

## 位置づけ

このリポジトリは、Agentの業務方針やXアカウントごとの文体を所有しません。それらは利用側のProjectで管理し、ここでは安全な共通能力だけをバージョン管理します。
