---
name: ai-native-design
description: AIチャット、Agent UI、reasoning summary、tool call／approval、streaming、sources、artifact、multi-agent、generative UI、AI dashboardなど、AI固有の状態・操作を含むWeb UIを調査・設計・実装・review・改善するときに使う。AI固有interactionを含まない一般的なLP、ブランドサイト、通常Web UI、backendだけの変更には使わない。
license: MIT. See LICENSE.txt
metadata:
  claudagt.version: "0.2.0"
  claudagt.status: "active"
---

# ai-native-design — AI-native UIの探索・選定・統合・検証

## 目的

対象Projectを先に理解し、最新の一次情報から再利用可能なUIを探索し、既存design systemへ最小差分で統合し、AI固有の非同期状態と品質を検証する。これはコンポーネント一覧ではなく、AI-native interfaceを実装するための実行プロトコルである。

## 使用するKnowledge

### Required

- `references/source-strategy.md`: sourceの役割、探索順序、採用・ライセンス判断。着手前に最後まで読む。

### Conditional

- 条件: 表示するAI interactionと状態を設計・変更するとき
  参照: `references/ai-ui-patterns.md`
- 条件: 実装、review、完了判定を行うとき
  参照: `references/quality-gates.md`

外部仕様は変わる。保存済みの知識や固定リストを正本にせず、実行時に上記資料から公式docs、公式repository、公式registryを開いて確認する。

## Source priority

主張と実装判断には次の順でsourceを使う。

1. 対象Project自身のコード、manifest、設定、design system、規約
2. 各libraryの公式documentation
3. 各libraryの公式GitHub repository
4. 公式registryとCLIが返す現在のmetadata / source
5. 21st.devなどのcommunity source

ブログ、SEO記事、古いsnippetは正本にしない。community候補の説明と実コードが食い違う場合は、実コード、依存関係、license原文を確認し、不明なら採用しない。

## 実行手順

### 1. Projectを調査する

実装前にrepository root、局所規約、対象画面、関連testを確認し、少なくとも次を証拠付きで把握する。

- frameworkとReact / Next.js等のversion、package manager、lockfile
- Tailwindの有無とversion、shadcn導入状況、`components.json`、component配置とimport alias
- 既存component library、共通layout、design tokens、CSS variables、typography、spacing
- light / dark theme、icon / animation library、responsive方針、accessibility方針
- 対象画面のdata model、server / client境界、streaming transport、error処理、test / lint / build command

名前だけで導入状況を推測しない。既存Projectがshadcnを使っていなければ、導入コスト、依存衝突、theme移行、保守責任と利点を比較し、無条件に導入しない。

### 2. 要件と状態を限定する

利用者の目的、主要action、表示してよいdata、対象viewportを定義する。`references/ai-ui-patterns.md` から関連する状態だけを選び、状態遷移、user actionとagent action、cancel / retry / approvalの責任主体を決める。内部Chain of Thoughtは要件に含めず、表示可能なsummary、progress、execution step、tool activityへ置き換える。

### 3. 再利用候補を探索する

次の順序を崩さない。

1. 対象Projectの既存component、pattern、tokenで解決できないか調べる。
2. 通常UI primitiveとdesign-system基盤はshadcn/uiを確認する。
3. 一般的なAI固有の意味・状態・interactionはVercel AI Elementsを第一候補として確認する。
4. tool-heavy、coding-agent、subagent中心のUIでは、Vercel AI Elementsと21st Agent Elementsを明示的に比較する。
5. visual表現や別案が必要な場合だけ、一般の21st.dev Marketplaceを公式CLI、MCP、または公式に認められた取得経路で探索する。
6. 適切な既存解がない場合だけcustom implementationを検討する。

同じ画面でsourceを組み合わせてよいが、役割を混同しない。ButtonやDialogをAI固有部品として再発明せず、Tool Stateを装飾だけのCardへ押し込めない。候補ごとにProject適合性、依存差分、accessibility、responsive、theme、保守性、license、更新経路を比較する。

### 4. 採用判断を記録する

採用sourceとcomponent / pattern、既存Projectで再利用するもの、棄却候補と理由を短く記録する。custom implementationは次のいずれかを具体的に示せる場合だけ選ぶ。

- 既存実装が要件を満たさない。
- 既存依存やarchitectureと大きく衝突する。
- accessibility、bundle、performance、licenseに許容できない問題がある。
- design systemへの統合コストが過大である。
- product固有interactionに既存patternがない。

「好み」「早そう」「見た目を変えたい」だけを再発明の理由にしない。採用sourceのversion、commitまたはregistry item、取得日、licenseとnotice維持条件を記録する。21st.dev Marketplaceの候補はMarketplace Termsと個別licenseを分けて確認し、license不明のcodeを取り込まない。

### 5. 既存systemへ統合する

- 現在のpackage managerとcomponent配置を使い、必要なcomponentだけを導入する。
- registry / CLI導入前に現在のsourceと依存差分をpreviewし、無関係なfileやtokenを上書きしない。
- 既存のcolor、radius、spacing、typography、focus、theme tokenへ合わせる。外部demoのbrand stylingをそのまま移植しない。
- stateを型で表し、表示とbackend / stream eventの対応を明確にする。重複componentや不要なdependencyを追加しない。
- model output、Markdown、tool result、artifactをuntrusted inputとして扱い、sanitize、URL scheme allowlist、runtime schema validation、sandboxを該当範囲で実装する。
- approval UIをserver-side authorizationの代替にしない。server側でscope、権限、idempotency、replay、timeout後の実行状態を検証する。
- streamingで既存内容を不必要に再mountせず、layout shift、scroll jump、focus lossを抑える。
- progressive disclosureを使い、contentよりUI chromeやanimationを目立たせない。

### 6. 関連gateを検証する

`references/quality-gates.md` のうち対象UIに関係するgateを選び、通常時だけでなく関連するloading、streaming、tool、approval、error、retry、empty、long-content状態を確認する。lint、typecheck、test、buildはProjectの既定commandを使う。未実行のgateを成功扱いしない。

### 7. 結果を報告する

実装結果と検証証拠を `出力契約` に従って返す。見た目が動くことと、production-readyであることを混同しない。

## Implementation decision rules

- Project既存componentが要件を満たすなら最優先で再利用する。
- 通常UIはshadcn/uiをfoundation候補とし、AI固有UIはAI Elementsを第一候補にする。ただしProject適合性の確認を省略しない。
- 21st Agent Elementsは21st公式のAI-native候補として、tool-heavy UIでAI Elementsと比較する。一般の21st.dev Marketplaceはdesign discoveryとして扱い、一律の品質保証済みsourceとみなさない。
- 公式sourceに適切な実装があっても、version、base、依存、APIが対象Projectと非互換なら採用を強制しない。
- visual fidelityより、情報階層、状態の明瞭さ、primary action、streaming安定性、user / agent actionの区別を優先する。
- 表示状態を増やす前に、その状態が利用者の判断またはactionを変えるか確認する。変えない状態は統合または省略する。

## Quality gate

関連する項目だけを適用するが、適用対象を恣意的に省略しない。

- design system統合、component / dependency重複、custom実装理由
- idleからcompletedまでの関連状態、tool状態、approval、error / retry、partial result
- keyboard、focus、semantic HTML、screen reader通知、reduced motion
- mobile / desktop、theme、overflow、long text / code / URL、large artifact、empty / network failure
- untrusted Markdown / HTML / URL / tool result、artifact sandbox、model生成props、server-side approval enforcement
- component再利用性、TypeScript type safety、lint、test、build

詳細な適用条件と証拠は `references/quality-gates.md` を使う。

## 出力契約

- 調査: 確認したProject stack、既存design system、関連component / tokenを示す。
- 選定: 採用sourceと理由、version / commit / registry item / 取得日、棄却した有力候補、custom実装なら必要性、外部codeならlicense / notice / Terms確認結果を示す。
- 実装: 変更file、追加dependency、状態とUIの対応を示す。dependency追加がなければ明記する。
- 検証: 実行したgate / commandと結果、手動確認した状態、security確認、未実行項目と理由を分離する。
- 完了: 要件を満たし関連gateが成功した場合だけ完了とする。source不明、license不明、互換性不明、検証不能は成功扱いせず、blockerと安全な次手を返す。

## 禁止事項

- Projectを調査せずにUI libraryや新しいdesign systemを導入しない。
- 適切な既存componentを合理的理由なく再実装しない。
- AI Elementsを確認せずにAI固有componentを独自実装しない。
- 21st.dev Marketplaceを自動scrapingしない。公式CLI / MCP等を使い、code、demo asset、media、説明、tagなどをTerms・license・依存・品質未確認でコピーまたは再配布しない。
- 内部Chain of Thoughtや秘密の推論tokenをそのまま表示する設計を標準化しない。
- AI生成HTMLを無条件に `dangerouslySetInnerHTML` へ渡さず、危険なURL、未検証props、active SVG、unsandboxed iframeをrenderしない。
- approval clickだけを権限確認とみなさず、server-side authorizationを省略しない。
- external sourceの大量なdocumentationやsource codeをSkillへ複製しない。
- static mockだけでstreaming、tool、approval、errorの品質を保証したと主張しない。
- 実行していないaccessibility、test、lint、buildを成功と報告しない。
- 秘密情報、認証情報、個人情報、private sourceを成果物や検証logへ混入させない。
