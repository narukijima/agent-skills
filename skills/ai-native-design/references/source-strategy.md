# Source strategy

AI-native UIの候補を、対象Projectへの適合性と検証可能性で選ぶ。ここに固定component catalogを作らず、実行時に一次sourceを再確認する。

## Contents

- Authority order / Project inventory
- Source roles: shadcn/ui、Vercel AI Elements、21st Agent Elements、21st.dev Marketplace、custom implementation
- Selection record

## Authority order

1. 対象Projectのcode、manifest、lockfile、design system、規約
2. libraryの公式documentation
3. libraryの公式GitHub repository
4. 公式registryとCLIが返すmetadata / source
5. community source

二次記事は発見の入口に限る。採用判断は上位sourceへ戻って確認する。主張の種類ごとに正本を選び、API / compatibilityはdocsと実source、licenseはlicense原文、Marketplace利用条件はTerms原文で確認する。確認日時、version、commitまたはregistry item URLを残す。

## Project inventory

候補探索より前に次を確認する。

| Area | Evidence examples | 判断への影響 |
| --- | --- | --- |
| Runtime | package manifest、lockfile、framework config | React / Next.js等のversionとserver / client境界 |
| Package management | lockfile、workspace config、scripts | install commandとdependency ownership |
| Styling | Tailwind / PostCSS config、global CSS、CSS Modules | component sourceの適合性と移植量 |
| Design system | `components.json`、shared component、Storybook | 再利用候補、base、alias、配置 |
| Tokens | CSS variables、theme、font、spacing、radius | 外部styleを既存visual languageへ統合できるか |
| Interaction | icon、animation、toast、form library | dependency重複とbehavior一貫性 |
| Quality | lint、typecheck、test、build、a11y規約 | 完了gateと利用可能な検証手段 |

shadcn未導入を「欠落」と決めつけない。既存libraryで同じ責務を満たせるなら、そのarchitectureを維持する。

## Source roles

### shadcn/ui — UI foundation

通常のprimitive、navigation、layout、form、overlay、feedback、data displayの基盤候補として使う。対象Project内の既存componentを先に確認し、その次にshadcn/uiを確認する。

実行時に確認する公式source:

- [shadcn/ui components](https://ui.shadcn.com/docs/components)
- [shadcn/ui registry documentation](https://ui.shadcn.com/docs/registry)
- [shadcn/ui GitHub repository](https://github.com/shadcn-ui/ui)

確認項目:

- 対象Projectのframework、Tailwind、base、React versionとの互換性
- `components.json`、import alias、CSS variables、既存primitiveとの重複
- registry itemが追加・変更するfileとdependency
- keyboard / focus behavior、theme、responsive、必要なcomposition

sourceをProjectへ所有するmodelでも、registry codeを盲目的に上書きしない。現在のCLIがpreview / diff機能を持つ場合は先に利用し、導入対象を最小化する。

### Vercel AI Elements — AI-native components

Conversation、Message、Prompt Input、Reasoning、Tool、Sources、Artifactやagent / streaming関連など、AI固有の意味・状態・interactionを持つUIの第一候補として使う。名前の記憶ではなく、現在の公式docs / registryから存在とAPIを確認する。

実行時に確認する公式source:

- [AI Elements documentation](https://elements.ai-sdk.dev/docs)
- [AI Elements components](https://elements.ai-sdk.dev/)
- [Vercel AI Elements GitHub repository](https://github.com/vercel/ai-elements)

確認項目:

- 対象ProjectのReact / Next.js、AI SDK、shadcn、Tailwind、CSS variablesとの互換性
- componentが表現するAI stateと、対象backend / stream eventの対応
- registry itemのsource、追加dependency、既存componentへの変更
- accessibility、streaming時のrender安定性、error / retry、customization surface

AI Elementsを使うためだけにProject architectureを全面移行しない。非互換なら、conceptを参照して既存design system上へ実装する判断も可能だが、その理由を記録する。

### 21st Agent Elements — tool-heavy AI-native candidate

tool card、Bash / Edit / Search / Todo / Plan、clarifying question、subagent、MCP、approvalなど、agent実行を中心にしたUIの比較候補として使う。一般のMarketplaceと同一視せず、21st公式repository / registryから取得する。

実行時に確認する公式source:

- [21st Agent Elements GitHub repository](https://github.com/21st-dev/agent-elements)
- [21st Agent Elements registry](https://agent-elements.21st.dev/r/index.json)
- [21st Agent Elements documentation](https://agent-elements.21st.dev/llms-full.txt)

確認項目:

- React、Tailwind、shadcn、Vercel AI SDK、base componentと対象Projectの互換性
- 対象tool / approval / subagent stateを実backend eventへ対応付けられるか
- AI Elementsと比較した追加dependency、design-system統合量、accessibility、保守性
- exact registry item、取得日、upstream version / commit、MIT license原文

AI Elementsを常に棄却する理由にはしない。conversation / sources / artifact中心ならAI Elementsを第一候補に保ち、tool-heavy要件があるときだけ両者を同じ要件表で比較する。

### 21st.dev Marketplace — design discovery

visual表現、interaction pattern、layout、比較候補を発見する場として使う。多くの作者とstyleを含むcommunity Marketplaceであり、ProjectのUI foundationや一律の品質保証として扱わない。

実行時に確認する公式source:

- [21st.dev component registry](https://21st.dev/)
- [21st.dev Terms of Service](https://21st.dev/terms)

公式CLI、MCP、または21stが明示的に認めた取得経路だけを使う。Marketplace Webページをbot、crawler、汎用scraperで自動収集しない。preview画像、動画、説明文、title、tagなどのmedia / structured metadataを成果物へ転載しない。

候補ごとに次を確認する。

1. author、original source、component URL、取得時点を特定する。
2. component codeと全dependencyを読む。preview画像だけで判断しない。
3. componentまたはoriginal repositoryのlicense原文と適用範囲を確認する。
4. 個別component licenseとMarketplace Termsを別々に確認する。商用利用、改変、再配布、attribution、元component pageへのlinkなど対象Projectに関係する条件を確認する。法的判断が必要なら担当者へ上げる。
5. keyboard、focus、semantic HTML、screen reader、reduced motionを検査する。
6. responsive、theme、long content、performance、保守性、既存tokenへの統合量を評価する。

licenseが見つからない、author / sourceが追跡できない、条件が曖昧なcodeは取り込まない。visual referenceとして使う場合も、表現を自分のdesign systemで再構成し、demo assetやmetadataを流用しない。

### Custom implementation — last resort

上位候補を実際に調査した後に限る。要件不足、architecture衝突、accessibility、bundle / performance、license、統合コスト、product固有interactionのどれが決定理由かを残す。customでも既存primitive、token、state modelは再利用する。

## Selection record

候補が複数ある場合は次の最小recordを残す。

| Candidate | Role | Compatibility | Dependencies | A11y / states | License / provenance | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Project existing | reuse | evidence | delta | evidence | Project-owned | adopt / reject reason |
| Official candidate | foundation / AI-native | evidence | delta | evidence | version / commit / registry item / license | adopt / reject reason |
| Community candidate | discovery | evidence | delta | evidence | exact license + Marketplace Terms | adapt / reject reason |
| Custom | last resort | evidence | delta | plan | Project-owned | explicit necessity |

採用理由だけでなく、最も有力な棄却候補の理由も短く残す。これにより、独自実装が単なる見落としではないことを確認できる。
