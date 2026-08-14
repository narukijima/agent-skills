---
updated_at: 2026-08-15
---

# Current State

## 現在の到達点

共有Skill配布元は明示import、provenance記録、全体validator、unit test、behavior eval schemaを持つ。
ClaudAGT AgentのIndependent Projectとして継続開発するProject契約を導入した。
モデル更新に伴う全体再監査を実施し、`sns-api` v2.0.0でmanifest v2互換とstatusの未使用`--operation`を廃止、
`sign-standing-authorization` CLIとreconcile capability宣言を追加、Instagram/Threads共通処理を
`meta_common`へ統合した。validatorはreference整合とscripts compile検査、importerは`_template`と
dirty source拒否を追加した。

## 現在の目標

対象契約: `PROJECT.md#PC-01`

Skillの発動契約、決定的処理、test、利用側責務境界を一致させ続ける。

## 目標の合格条件

- 対象Skillのfrontmatter、references、scripts、tests、catalogが同じCapabilityを示す。
- 全体validatorと対象testが合格する。

## 検証結果

- 対象: `PROJECT.md#PC-01`
- 確認日: 2026-08-15
- 方法: `git diff --check`、`bash tools/validate-skills.sh`、`python3 -m unittest discover -s tests -q`、
  `python3 tools/score-behavior-eval.py --cases evals/<skill>/cases.json`（3定義）。
- 結果: validatorと167件のunit testが合格した。behavior eval 3定義50 caseもschema検証に合格した。

## 未完了・ブロッカー

- 既知のブロッカーはない。

## 現在有効な決定

- `agent-skills`は共有配布元であり、ClaudAGT Agentへ必要なSkillだけをprovenance付きで明示importする。
- 現在判断ではactiveなKnowledgeとSkillを優先し、非activeな参照は履歴確認時だけ使う。

## 失敗・却下済み

- 全Agentへの自動同期: 利用側ownershipと再現可能な出所を失うため採用しない。

新しい根拠または利用者の明示指示がない限り、ここにある方法を繰り返さない。

## 次の一手

1. 次の利用者scopeに対して対象Skillの発動契約、決定的処理、testを同時に確認する。
