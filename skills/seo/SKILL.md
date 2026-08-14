---
name: seo
description: SEO、検索順位・流入低下、crawl/indexing、robots、sitemap、canonical、Search Console、Core Web Vitals、structured data、internal linking、pSEO、AIO/AEO/GEO、AI searchの調査・実装・検証に使う。広告、SNS、SEO目的のないcopywritingや一般UI改善には使わない。
license: MIT. See LICENSE.txt
metadata:
  claudagt.version: "0.3.0"
  claudagt.status: "active"
  claudagt.aliases: "technical seo,search optimization,search console,aio,aeo,geo,pseo,ai search"
---

# seo — 証拠から検索可視性を診断し、最小修正を再検証する

## 目的

対象Projectと実測dataを正本にし、`Observe → Measure → Diagnose → Fix → Verify` を一つの追跡可能なprotocolとして実行する。SEO知識の列挙やscore改善ではなく、重要な問題を絞り込み、coding agentとして必要な最小変更と検証まで扱う。

## 使用するKnowledge

### Required

- `references/source-policy.md`: source priority、時間依存情報、主張の確度、証拠の記録方法。着手前に最後まで読む。

### Conditional

- 条件: HTTP、crawl、indexing、canonical、sitemap、rendering、architecture、internal link、hreflang、migrationを調べるとき
  参照: `references/technical-search.md`
- 条件: title、snippet、heading、content、search intent、競合、information architectureを評価するとき
  参照: `references/search-quality.md`
- 条件: Search Console、順位・流入低下、Core Web Vitals、検索・AI可視性を測定するとき
  参照: `references/measurement.md`
- 条件: Schema.org、JSON-LD、rich resultを調査・実装するとき
  参照: `references/structured-data.md`
- 条件: template / dataから多数URLを計画、生成、監査するとき
  参照: `references/programmatic-seo.md`
- 条件: AIO / AEO / GEO、AI検索、citation、crawler、robots制御、`llms.txt`等を扱うとき
  参照: `references/ai-search.md`

関係するreferenceだけを読み、無関係な分野を一括ロードしない。providerの仕様、bot名、rich-result対応、計測画面は変わるため、reference内のURLから実行時の公式documentationを確認する。

Source優先順位と証拠の記録方法は`references/source-policy.md`の`Source hierarchy`と`Evidence ledger`が正本である。下位sourceだけで重大変更を決めない。

## 実行protocol

### 1. Observe — 対象と期待状態を固定する

- repository root、局所指示、stack、build / test / deploy command、branch / dirty state、対象environmentを確認する。
- siteの目的、重要URL群、対象engine / locale / device、期待するcrawl・index状態、調査期間を定義する。
- access可能なSearch Console、analytics、server log、CDN / WAF、crawl exportを確認する。accessがなくても進められる範囲を分離する。
- URL、robots、canonical、redirect、noindex、sitemap、deletion等のsite-wide変更は、問題の証拠が揃うまで実施しない。

### 2. Measure — 同じ対象を複数surfaceで測る

- HTTP status / headers / redirect、static HTML、rendered DOM、internal link、sitemap、robots、logを必要範囲で取得する。
- crawler検証ではUser-Agent文字列だけを信用せず、公式IP検証方法やSearch Console / logを使う。通常browser成功をcrawler成功の代用にしない。
- static snapshot、rendered output、provider側index stateを別列で記録する。単一page、`site:`検索、単一crawler、単一scoreからsite-wide結論を出さない。
- 取得時刻、environment、URL / property、user agent、collection method、sample / populationを証拠へ付ける。
- 保存HTMLまたはURL inventoryの機械的抽出が必要なら `scripts/seo_evidence.py` を使う。scriptのsignalをroot causeや最終Findingと同一視しない。

### 3. Diagnose — 観測と原因を分離する

各候補を次の順で確定する。

1. Observation: 実際に何が起きたか。
2. Evidence: どのsource・scope・時刻で確認したか。
3. Diagnosis: 期待状態との差と、最も説明力のあるroot cause。
4. Confidence: 根拠の強さ。
5. Severity: business / discovery / indexingへの影響。
6. Proposed fix: 原因を除く最小変更。
7. Verification plan: local、deployed、provider recrawl後の確認。

相関だけならroot causeを確定しない。代替仮説と、それを反証する次の測定を示す。問題が再現しなければ「SEOでは一般に推奨」の理由だけで修正へ進まない。

### 4. Fix — 診断済み範囲だけ変更する

- Findingと影響URL群を先に確定し、既存Projectの実装patternへ最小差分で直す。
- canonical、robots、redirect、sitemap、noindex、URL population、structured dataは互いのsignalを整合させる。
- large-scale生成はpilot cohortとindexation gateを先に通す。URL削除やmigrationはredirect / rollback / monitoringを用意する。
- 診断だけの依頼では外部service設定やproduction deployを変更しない。変更操作が依頼scope外なら実行せず、Skill独自のGeneric Runtime PermissionやHuman Approvalを追加しない。

### 5. Verify — 実装と外部反映を分離する

関連範囲について、Project既定のlint / test / buildに加え、変更内容に応じて次を再確認する。

- local / preview / productionのHTTP status、redirect chain、headers
- static HTMLとrendered DOMのrobots、canonical、hreflang、structured data、重要本文
- robots.txt、sitemap、internal link、代表URLと影響population
- performance変更のlab regressionと、利用可能ならfield data / RUM
- deployされたartifactとruntime設定がsource変更に一致すること

Search Engineの再crawl / 再処理が必要なら、`verified implementation` と `pending external recrawl` を分ける。観測していない回復時刻を約束せず、monitor条件、owner、確認日を残す。

## Task routing

| 依頼 | 最初のmeasurement | 主なreference |
| --- | --- | --- |
| crawl / index障害 | crawler別HTTP、robots、headers、log、Page Indexing | `technical-search.md` |
| canonical / duplicate | requested / final / declared / engine-selected URLの比較 | `technical-search.md` |
| sitemap問題 | sitemap全URLとstatus / indexable / canonicalのjoin | `technical-search.md` |
| 順位・流入低下 | 期間比較をquery / page / country / device / search typeへ分解 | `measurement.md` |
| Core Web Vitals | CrUX / RUM field data、次にlab reproduction | `measurement.md` |
| structured data | visible content、static / rendered JSON-LD、Schema.org、consumer eligibility | `structured-data.md` |
| pSEO | demand、intent、entity / data、URL population、unique utilityのgate | `programmatic-seo.md` |
| AI search / crawler | provider公式docs、search / training / user fetchの用途別control | `ai-search.md` |
| on-page / architecture | query intent、SERP、content、link graph、重要pageの実績 | `search-quality.md` |

## Evidence and decision rules

主張の確度分類（`confirmed` / `likely` / `hypothesis` / `unsupported`）は
`references/source-policy.md#claim-classification`が正本である。

### Severity

- `Critical`: 重要population全体のcrawl / index / serving停止、重大なspam / security action、または同等の事業影響。
- `High`: 重要URL群の発見・canonicalization・indexing・trafficを大きく損なう。
- `Medium`: 限定scopeの可視性、理解、snippet、performanceを継続的に損なう。
- `Low`: 影響が小さい改善、保守性、将来risk。ranking上昇を保証しない。

### Confidence

- `Confirmed`: 現象と原因の直接証拠がある。
- `High`: 強い複数証拠があり代替説明が弱い。
- `Medium`: 妥当だが未検証の前提またはsample制約がある。
- `Low`: 仮説段階。

severityとconfidenceを独立して付ける。Critical / Low confidenceの候補は、緊急変更ではなく緊急検証を要求する。

## Quality gates

- scope、baseline、期待状態、取得時刻が記録されている。
- static / rendered / indexed、field / lab、declared / selected canonicalを混同していない。
- FindingごとにEvidence、Impact、Confidence、Severity、Root cause、Fix、Verificationがある。
- fix前に問題を再現し、fix後に同じmethodで再測定している。
- official仕様が時間依存なら実行時に再確認している。
- external recrawl、insufficient data、permission不足、未実行testをpassにしていない。

## 出力契約

監査・診断では件数を埋めるchecklistではなく、重要度順のFindingを返す。

```text
Issue:
Evidence:
Impact:
Confidence: Confirmed | High | Medium | Low
Severity: Critical | High | Medium | Low
Root cause:
Fix:
Verification:
```

加えて、調査scope、測定期間、使用source、変更file / setting、実行command、検証結果、未確認事項を示す。Findingがなければ「問題なし」ではなく、確認できた範囲と検出限界を返す。

## 禁止事項

- title / meta description /本文を任意の固定文字数へ合わせることをranking要件としない。
- H1数、click depth、subfolder / subdomain、滞在時間、bounce rate等を普遍的ranking ruleとして扱わない。
- schema、`llms.txt`、crawler許可、content chunking、特定文体が順位やAI citationを上げると根拠なく断定しない。
- search crawler、training crawler、user-triggered fetch、provider内の別product controlを混同しない。
- static fetchだけでrendered schema / contentが存在しないと断定しない。
- Search Consoleのaverage positionを固定順位、exportを全query母集団、`site:`検索をindex coverageとして扱わない。
- 有料vendor、単一SEO score、保存済みbot一覧を前提条件にしない。
- 診断なしにcanonical、robots、redirect、sitemap、noindex、internal links、URL、page deletionを変更しない。
- code変更だけで完了とせず、deploy / runtime / external recrawlの状態を偽らない。
- 秘密情報、実property data、実API response、個人情報をSkill、test fixture、log、commitへ保存しない。
