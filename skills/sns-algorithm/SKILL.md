---
name: sns-algorithm
description: SNSアルゴリズムの公式一次情報をsurface単位で検証し、投稿・アカウント・運用データの診断、比較、仮説、実験設計を行う。SNS文章作成やAPI実行だけの依頼には使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.1.1"
  agent-directory.status: "active"
  agent-directory.aliases: "sns algorithm,social algorithm,ranking algorithm,recommendation algorithm,投稿分析,アルゴリズム分析"
---

# sns-algorithm

## 目的

X、YouTube、Facebook、Instagram、Threads、TikTokの公開された公式一次情報を、推薦・ランキング・検索・配信surfaceごとに整理し、観測データと結びつけて診断、比較、改善仮説、最小実験を作る。

常に `Evidence → Algorithm Model → Analysis → Action` の順で考える。攻略法や成功保証を作らず、確認済みmechanism、分析上の推論、検証前の仮説、公開情報のない部分を分離する。

## 発動条件

- `sns-algorithm` が明示されたとき。
- SNS投稿・動画・アカウント・運用戦略の結果を、推薦、ランキング、検索、配信、eligibilityの観点で分析・比較・診断するとき。
- 「伸びなかった理由」「急に止まった理由」「アルゴリズム的に評価されるか」「この情報は本当か」「次のA/B test」を問われたとき。
- X、YouTube、Facebook、Instagram、Threads、TikTokのalgorithm、ranking signal、recommendation surface、search discovery、distributionを説明するとき。

次だけなら発動しない。

- 投稿文、caption、画像、動画の作成だけ。
- 投稿、認証、API read/write、schedule、recoveryだけ。これらは `$sns-api` または利用側Projectの責務。
- 有料広告の配信最適化だけ。

## 使用するKnowledge

### Required

- claimの作り方と共通stage: `references/methodology.md`
- evidence class、source優先順位、freshness: `references/evidence-policy.md`
- 診断、比較、lever翻訳、実験設計: `references/analysis-framework.md`

### Conditional

必要なplatformとsurfaceだけを読む。

- source・重要claimの出典またはfreshnessを照合するとき（Algorithm model / Freshness gate）: `references/source-registry.json`
- surfaceを未特定、またはplatform横断比較: `references/platform-matrix.md`
- X: `references/platforms/x.md`
- YouTube: `references/platforms/youtube.md`
- Facebook: `references/platforms/facebook.md`
- Instagram: `references/platforms/instagram.md`
- Threads: `references/platforms/threads.md`
- TikTok: `references/platforms/tiktok.md`

## Freshness gate

1. 「最新」「現在」「今のalgorithm」、重要な運用判断、数値weight、eligibility変更では、利用可能なら公式一次情報を再確認する。
2. `source-registry.json` の `last_verified`、sourceのpublished/updated date、version/commitを確認する。ネットワークがなければrecorded時点を明示する。
3. Xのcode claimはrecorded commitとcode pathを引用する。`main` の値を永続仕様にしない。current upstreamが違えばclaimを再検証する。
4. 公式資料が更新日を示さない、または保存claimと現行資料の一致を確認できない場合は `stale` へ下げる。過去資料を現在形で使わない。

## 分析protocol

1. **Platform** — platformを一つ以上特定する。platform間でsignalを転用しない。
2. **Surface** — Feed、Reels、Home、Search等を特定する。不明ならsurface候補を分けて分析する。
3. **Goal** — reach、qualified view、watch time、search discovery、conversion等、利用側ProjectのKPIを入力として受け取る。Skillへ固定しない。
4. **Observed evidence** — impression、view、source、retention、CTR、engagement、negative feedback、時系列、比較対象、eligibility表示を「観測」として列挙する。欠損を0扱いしない。
5. **Algorithm model** — registryとplatform referenceから、そのsurfaceで公式確認されたeligibility、retrieval、ranking、re-ranking、filtering、distribution、feedbackを照合する。公開されないstageは `unknown`。
6. **Drivers** — creator-controllable、viewer/context、platform/system、外部要因に分け、複数の原因候補をconfidence順に置く。
7. **Alternatives** — topic demand、competition、seasonality、audience mix、measurement window、creative差、policy/eligibilityを含む代替説明を検討する。
8. **Uncertainty** — correlationをcausationにしない。反証に必要なdataと、まだ言えないことを明示する。
9. **Action** — mechanismからleverへの推論を明示し、最小のexperimentを一つずつ設計する。

説明だけの依頼では診断templateを強制しないが、claimにはsurface、evidence class、freshnessを保持する。

## Output contract

診断・比較では、必要な粒度で次を返す。

- `platform` / `surface`
- `observed_evidence`
- `confirmed_algorithm_mechanics`（evidence classとsource IDを伴う）
- `likely_drivers`（各driverのconfidenceと推論経路）
- `alternative_explanations`
- `unknowns`
- `recommended_experiment_or_action`（仮説、変更変数、固定条件、primary metric、guardrail、期間/標本、停止条件）
- `source_freshness`

confidenceは `high` / `medium` / `low` を使い、evidence classとは別にする。data不足は低confidenceと追加data要求で表し、単一原因へ収束させない。

## sns-apiとの境界

- このSkillは知識、分析、診断、評価、仮説、実験設計を所有する。
- credentials、provider request、投稿、削除、auth、ledger、recovery、stateを所有せず、外部作用を実行しない。
- `$sns-api` が取得したanalyticsや投稿dataは入力にできるが、取得されていないdataを捏造しない。
- 分析結果から公開を提案できるが、実行は利用側Projectと `$sns-api` の別intentにする。

## 禁止事項

- 非公式攻略法、SEO blog、consultant、influencer発言を公式仕様にしない。
- 出典、surface、version/commitなしにweightを作らない。資料の列挙順を重要度順と解釈しない。
- correlationをcausationとして断定せず、「必ず伸びる」「バズる」を保証しない。
- 一つのplatformを一つのalgorithmとして扱わず、別platformのsignalを移植しない。
- Instagram/Facebookの仕組みをThreads固有仕様として扱わない。
- follower数、投稿頻度、投稿時刻を根拠なくranking signalにしない。
- `shadowban` を初期診断にせず、recommendation eligibility、policy enforcement、account restriction、需要、競争、計測問題を分離する。
- Project固有KPI、文体、投稿時間、brand ruleを共通知識へ固定しない。
- 公開情報のないstageを標準的recommender architectureから補って断定しない。
