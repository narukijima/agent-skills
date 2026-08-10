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

インポートは変換なしのvendored copyです。既存の同名Skillは自動上書きせず、コピー元のrepository、commit SHA、`metadata.claudagt.version`、インポート時刻を `skills/x-api/agents/upstream.yaml` に記録します。Skill内の `LICENSE.txt` とthird-party noticeもコピー対象です。更新は利用側で差分を確認してから、明示的に再インポートします。

## 公式Skill

### ai-native-design

既存design systemを先に調査し、一般UIは既存component / shadcn/ui、AI-native UIはVercel AI Elements、tool-heavy UIは21st Agent Elements、一般の21st.dev Marketplaceは規約に沿ったdesign discoveryとして比較します。AI固有state、untrusted generated content、server-side approval、accessibility、responsive、検証証拠までを一つの実行protocolで扱います。

### logic-pro

Logic Pro専用MCPまたは互換アダプタを利用し、状態確認、transport、track、内蔵音源、MIDI取込、保存、bounceをProject境界と独立読戻し付きで扱います。特定MCPのtool名には依存せず、実行時capabilityを意味操作へ対応付けます。macOS向けには、状態・Project・Play/Stopを日英Accessibility labelと独立読戻しで扱う自己完結reference adapterを同梱します。

### x-api

X API v2の読み取りと、dry-runを標準にした通常投稿です。返信・引用・いいね・フォロー・DM・削除・ブラウザ操作は含めません。投稿の実行には、ユーザーコンテキスト、`X_POSTING_ENABLED=true`、送信台帳、`--live` が必要です。ユーザーコンテキストの第一経路は失効しないOAuth 1.0a（長期運用エージェント向け）で、OAuth 2.0 userトークンも利用できます。

```bash
python3 skills/x-api/scripts/x_api.py --pretty me
python3 skills/x-api/scripts/x_api.py --pretty user --username XDevelopers
python3 skills/x-api/scripts/x_api.py --pretty search-recent --query 'from:XDevelopers -is:retweet'
python3 skills/x-api/scripts/x_api.py --pretty post --dry-run --text 'draft only'
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
