---
name: agent-skills
description: agent-directory互換の再利用Capabilityを自己完結したSkillとして安全に開発・検証・配布する
status: active
mode: continuous
---

# `agent-skills`

## 目的

複数Agentで再利用できるCapabilityを、発動契約、Domain Safety、決定的処理、出所記録を備えた
自己完結Skillとして提供する。

## 継続的使命

> 必要なSkillだけを利用側Agentが明示importできる、検証可能で安全な公式配布元を維持する。

## 成功指標

- **PC-01** 各Skillのfrontmatter、references、scripts、testsが同じCapability契約を表す。
- **PC-02** 全体validatorと対象Skill testが合格し、secretや実運用識別情報を含まない。
- **PC-03** import Toolが既存Skillを上書きせず、source repository、commit、versionを記録する。

## 見直し・終了条件

- 再利用Capabilityの配布責務が別の正本へ完全移管されたとき、継続または終了を見直す。
- agent-directoryのSkill schemaまたは主要Provider APIが変わったとき、互換性と安全契約を見直す。

## 判断原則

- SkillはReusable CapabilityとDomain Safetyを所有し、Runtime Permissionや利用側の意思決定を所有しない。
- 配布元と利用側copyを分離し、自動同期せず明示importとprovenanceを使う。

## 非ゴール

- account固有人格、投稿内容、運用戦略、scheduleを共有Skillへ埋め込むこと。
- 全AgentへSkillを自動同期すること。

## 制約・固定決定

- 公開repositoryへの外部作用はClaudAGT rootの公開境界に従う。
- Git author/committer emailは承認済みGitHub noreplyだけを使う。
- secret、実運用account、実API responseをcommitしない。

## 品質基準

- Skill追加・変更後はrepository全体validatorと対象Skill testを実行する。
- static schema検証だけをbehavior証明として扱わない。

## 入力

- 利用者指示、公式API仕様、再現可能な不具合、対象Skillのtest fixture。

## 使用するKnowledge

### Required

- なし

### Conditional

- なし

## 使用するSkill

### Required

- なし

### Conditional

- なし

## 成果物

- 各`skills/`下のSkill directory、`evals/`、`tests/`、`tools/import-skill.sh`、`tools/validate-skills.sh`。

## 検証方法

- 実行手順: `bash tools/validate-skills.sh`、`python3 -m unittest discover -s tests -v`、対象Skill固有testを実行する。
- 合格条件: PC-01からPC-03に関係する全validatorとtestがexit 0となる。
- 不合格時の扱い: 未完了として`STATE.md`へ失敗理由と次の一手を残す。
- 必要な環境変数: live API testを明示実行する場合だけ対象Skillの`.env.example`記載key。
- 使用した入力: Git working tree、repository内fixture、明示された公式仕様。
