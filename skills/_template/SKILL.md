---
name: <skill-name>
description: <200文字以内。何を行い、どの依頼で使うか、誤発動を避ける境界が分かる説明>
license: MIT. See LICENSE.txt
metadata:
  claudagt.version: "0.1.0"
  claudagt.status: "active"
  claudagt.aliases: "<comma-separated aliases>"
---

# `<skill-name>` — <一行説明>

## 発動条件

- 利用者が `<skill-name>` を明示したとき
- <このSkillを選ぶべき具体的な依頼>

## 目的

<このSkillが所有する共通能力。利用側のProject固有の方針は書かない。>

## 使用するKnowledge

### Required

- なし

### Conditional

- 条件: <追加資料が必要になる条件>
  参照: `references/<reference-file>.md`

## 手順

1. <入力と前提を確認する>
2. <必要な資料だけ読む>
3. <決定的なスクリプトがあればそれを使う>
4. <出力契約と安全ゲートを確認する>

## 出力契約

- 成功: <返す形式>
- 不明: <成功扱いせず返す形式>
- 失敗: <非0または利用者へ上げる条件>

## 禁止事項

- 秘密情報を出力・保存・コミットしない。
- 利用側Projectの方針、文体、KPIをこのSkillへ持ち込まない。
- `scripts/` の決定的処理をAgentの都合で再実装しない。
