---
updated_at: 2026-08-15
---

# Current State

## 現在の到達点

共有Skill配布元は明示import、provenance記録、全体validator、unit test、behavior eval schemaを持つ。
ClaudAGT AgentのIndependent Projectとして継続開発するProject契約を導入した。
最新モデル能力に伴う包括的再監査で、`sns-api` の `resolve_workspace_root` における環境変数 `AGENT_DIRECTORY_ROOT` 対応および親探索境界（`stop_at`）保護を導入し、上位Gitリポジトリへの探索漏れによる誤検出を解消した。
import元のignored fileがprovenance外で混入する経路を、記録済みcommitのGit treeだけを展開する方式へ単純化して閉じた。
その時点で全体validator、全168件のunit test、および3定義50ケースのbehavior eval定義検証が完全合格する状態を確立した。
`sns-algorithm` Skillを追加し、X / YouTube / Facebook / Instagram / Threads / TikTokをsurface単位で扱うEvidence → Algorithm Model → Analysis → Action契約、27件の公式sourceと38件の重要claimを持つsource registry、registry validator、14件のbehavior evalを自己完結した配布物として統合した。Xの数値claimは`xai-org/x-algorithm`のfull commit SHAとcode pathへ固定し、`sns-api`の実行責務とは分離した。

## 現在の目標

対象契約: `PROJECT.md#PC-01`

Skillの発動契約、決定的処理、test、利用側責務境界を一致させ続ける。

## 目標の合格条件

- 対象Skillのfrontmatter、references、scripts、tests、catalogが同じCapabilityを示す。
- 全体validatorと対象testが合格する。

## 検証結果

- 対象: `PROJECT.md#PC-01`
- 確認日: 2026-08-15
- 方法: `git diff --check`、`bash tools/validate-skills.sh`、`python3 -m unittest discover -s tests -v`、
  `python3 skills/sns-algorithm/scripts/validate_registry.py`、
  `python3 tools/score-behavior-eval.py --cases evals/<skill>/cases.json`（4定義64ケース、model実行・judgmentなし）。
- 結果: 4 Skillのvalidator、全179件のunit test、`sns-algorithm`の6 platform / 27 source / 38 claim registryが0 failure / 0 errorで合格し、behavior eval 4定義64 caseは`behavior_run: false`の定義検証として合格した。実model behaviorの成功を意味しない。

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
