# agent-skills

共有Skillの公開配布元。各Skillは自己完結した `skills/<name>/` とし、利用側Agentへ必要なSkillだけをコピーして導入する。

## 公開製品境界

- このrepositoryは`agent-directory`とは別のGit repository、remote、validation、配布境界を持つ。monorepo化や自動同期をしない。
- ここが所有するのは、再利用Capabilityの目的、Skill契約、Domain Safety、import/provenance、validator、test、利用者向け文書である。
- Owner Agentの現在目標、優先順位、到達履歴、次の一手、handoff、採用revisionはowner rootが所有する。rootの`PROJECT.md`や`STATE.md`をこの公開repositoryへ複製しない。
- Agent Directoryを利用する一般Projectの`PROJECT.md` / `STATE.md`契約は`agent-directory`が所有する。

## 製品不変条件

- 各Skillのfrontmatter、references、scripts、tests、catalogは同じCapability契約を表す。
- SkillはReusable CapabilityとDomain Safetyを所有し、Runtime Permission、利用側Projectの意思決定、account固有人格、投稿内容、運用戦略、scheduleを所有しない。
- CIやGitHub Actionsに依存しない。検証はローカルの`tools/validate-skills.sh`とtestsで完結させる。

## 変更ルール

- `skills/<name>/SKILL.md` のfrontmatterを発動契約の正本にする。metadataは`agent-directory.version` / `agent-directory.status` / `agent-directory.aliases`を使う。
- 詳細なAPI仕様は `references/`、決定的な実行処理は `scripts/` に置く。
- 秘密情報、実運用アカウント、実APIレスポンスをコミットしない。
- Git author/committer emailはGitHubのnoreplyだけを使い、私用メールを履歴へ記録しない。
- Skillの追加・変更後は `bash tools/validate-skills.sh` と `python3 -m unittest discover -s tests` を実行する。
- `tools/import-skill.sh` は既存Skillを上書きせず、コピー元のrepository・commit・versionを利用側の `agents/upstream.yaml` に記録する。

## Push Policy

gated
