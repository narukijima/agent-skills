# agent-skills

共有Skillの配布元。各Skillは自己完結した `skills/<name>/` とし、利用側Agentへ必要なSkillだけをコピーして導入する。

## 変更ルール

- `skills/<name>/SKILL.md` のfrontmatterを発動契約の正本にする。
- 詳細なAPI仕様は `references/`、決定的な実行処理は `scripts/` に置く。
- 秘密情報、実運用アカウント、実APIレスポンスをコミットしない。
- Skillの追加・変更後は `bash tools/validate-skills.sh` と対象Skillのテストを実行する。
- `tools/import-skill.sh` は既存Skillを上書きせず、コピー元のrepository・commit・versionを `agents/upstream.yaml` に記録する。
