---
name: comprehension-level
description: 文章、SNS、UI、図解、画像、動画などあらゆる人間向けアウトプットの最低理解レベル(ELI5〜Expert、auto)を認知制約へ変換して制御・検証する。内容の高度さと正確性は保ち、理解に必要な認知負荷だけを下げる。バズ、SEO、ブランド文体、媒体制作そのものには使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.1.0"
  agent-directory.status: "active"
  agent-directory.aliases: "eli5,eli8,eli10,eli12,eli15,comprehension level,cognitive accessibility"
---

# comprehension-level — 人間向けアウトプットの最低理解レベル制御

## 発動条件

- 利用者が `comprehension-level` を明示したとき、またはELI5 / ELI8 / ELI10 / ELI12 / ELI15 / Adult / Expert等のLevelを指定したとき
- 「わかりやすく」「一般向けに」「専門外でも理解できるように」等、理解可能性の制御を依頼されたとき
- 他Skillや利用者が生成した人間向けアウトプット(文章、SNS投稿、UI文言、図解、画像、動画構成)の理解レベルを指定・検証するとき

使わない場合: 内容自体の企画立案、バズ・SEO・ブランド文体の最適化、動画編集・UIデザイン等の媒体制作技術そのもの。

## 目的

あらゆる人間向けアウトプットの最低理解レベル(comprehension floor)を制御する共通Capability。内容の高度さと正確性は落とさず、理解するために必要な認知負荷だけを下げる。

基本構造は `Level → Cognitive constraints → Output`。ELI5〜Expertは人間が扱いやすい操作用proxyであり、年齢を科学的な絶対基準として扱わない。適用時は必ず認知制約(前提知識、語彙難易度、専門語の扱い、抽象度、推論段数、同時概念数、情報単位の複雑さ、作業記憶負荷、文脈依存性、視覚的即時理解性、操作・判断の認知負荷)へ変換してから出力へ適用する。

原則:

- 「12歳向けに話す」と「12歳程度でも理解できる」は別物。このSkillが扱うのは後者のfloorである。
- 大人向けのトーンを維持したまま、低い理解レベルへ対応できる。childishな言い回しを標準にしない。
- 難しい内容を削って薄くするSkillではない。認知負荷の原因だけを除去する。
- 専門語が必要なら禁止せず、必要な位置で理解可能にする。
- 正確性を犠牲にした単純化は失敗として扱う。
- readability scoreや学年換算値は補助信号であり、理解度の正本にしない。

このSkillはcomprehensibility / cognitive accessibilityだけを所有する。shell、filesystem、network、sandbox等のGeneric Runtime Permissionを設定・判定しない。

## 使用するKnowledge

### Required

- `references/levels.md`: 認知制約の定義、Level profile、`auto` の解決手順、根拠。Level適用前に読む。

### Conditional

- 条件: 特定媒体(prose / SNS / UI / diagram / image / video / short-form video)へ適用するとき
  参照: `references/media-rules.md`
- 条件: 生成・修正後のアウトプットが指定Levelを満たすか判定するとき
  参照: `references/validation.md`

## 手順

1. 対象アウトプット、媒体、目的、想定Audience、保持すべき事実・主張、指定Levelを確認する。指定がなければ `auto` として `references/levels.md` の解決手順でLevelを決め、判断根拠を1行で記録する。解決できなければ不明として返す。
2. Levelを認知制約profileへ変換する。年齢labelのまま「子供っぽく書く」等の直訳をしない。
3. 対象媒体での制約の現れ方を `references/media-rules.md` で確認し、適用する。媒体制作そのものは利用側または各制作Skillの責務。
4. 生成または修正する。内容の削除ではなく、認知負荷の原因(未接地の専門語、具体例のない抽象、長い推論チェーン、同時概念過多、過剰な文脈依存)を除去する。
5. `references/validation.md` のgateで検証する。failしたgateは修正して再検証し、未実行のgateをpass扱いしない。

## 出力契約

- 成功: 適用Level(autoの場合は解決根拠)、変換した認知制約、行った修正、validation gate結果、検証強度(`structural-pass`または`audience-verified`)を返す。
- 不明: Levelを解決できない場合(audience / medium / purposeが不明でdefaultも不適切)は、成功扱いせず候補Levelと必要な追加情報を返す。
- 失敗: validation gateがfailのまま解消できない場合、fail項目と理由を明示して返す。理解可能性と忠実性が衝突して解消できない場合は忠実性を優先し、その旨を報告する。

## 禁止事項

- 事実、条件、重要なnuanceを犠牲にした単純化をしない。
- 高度な内容の削除で見かけ上の平易化をしない。
- 大人向けアウトプットへchildish registerを無断で導入しない。
- readability score・学年換算値単独で完了判定しない。
- Project固有のAudience戦略、KPI、ブランド文体、投稿戦略をこのSkillへ持ち込まない。
- バズ・virality、SEO、SNSアルゴリズム、コピーライティング、美文生成、動画編集、UIデザインそのものを所有しない。
- 外部資料の大量コピーをKnowledgeへ持ち込まない。
- 秘密情報を出力・保存・コミットしない。
