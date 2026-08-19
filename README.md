# agent-skills

AIエージェントが共通利用するSkillの公開配布元です。各Skillは自己完結した `skills/<name>/` で、[Agent Skills標準](https://agentskills.io)の `SKILL.md` を正本とします。利用側は必要なSkillだけをvendored copyとして明示的に取り込み、各Runtime(Claude Code、Codex等)の標準Skill配置へ薄いadapterで接続します。

- 配布元: [`claudagt/agent-skills`](https://github.com/claudagt/agent-skills)
- 利用側の例: [`claudagt/agent-directory`](https://github.com/claudagt/agent-directory)

## 構成

```text
skills/<skill-name>/
├── SKILL.md      # 発動契約と入口。frontmatterが正本
├── LICENSE.txt
├── references/   # 条件付きで読む詳細
└── scripts/      # 決定的な実行処理
```

`SKILL.md` は短い入口、詳細仕様は `references/`、再実装を避ける固定処理は `scripts/` に置きます。Skill一覧と作成手順は [skills/SKILLS.md](skills/SKILLS.md) を参照してください。

## 導入

```bash
bash tools/import-skill.sh <skill-name> --target /path/to/consumer-root
```

自動import・自動同期はしません。既存の同名Skillを上書きせず、コピー元repository、commit SHA、versionを利用側の `skills/<skill-name>/agents/upstream.yaml` に記録します。コピー対象は記録したcommitのGit treeだけで、untracked / ignored fileは含めません。更新は利用側で差分を確認してから明示的に再importします。

## 検証

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests
```

validatorはSkill契約(frontmatter、catalog登録、reference整合、scripts compile)と公開リポジトリのsecret/PII scanを検証します。

## 責務境界

このRepositoryの責務は `Reusable Capability + Domain-specific safety` です。Runtime(shell、filesystem、network、sandbox、permission)を設定・判定せず、利用側Projectの意思決定(投稿内容、文体、schedule、account固有人格、KPI)を所有しません。Skill固有の安全境界(例: `sns-api` の署名manifest・budget・duplicate防止)はDomain Safetyとして各Skillが維持します。
