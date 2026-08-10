# Quality gates

対象featureに関連するgateだけを選ぶ。各gateを `applicable`、`not applicable`、`blocked` に分類し、`applicable` は証拠を残す。未実行をpassとしない。

## 1. Project and design-system fit

- 既存component、token、typography、spacing、radius、icon、motionを再利用している。
- 同等componentを重複作成していない。
- shadcn、AI Elements、community、customの役割と採用理由が説明できる。
- Projectがshadcn未導入の場合、導入または非導入の比較がある。
- 追加dependencyが必要最小限で、既存packageと重複・衝突していない。
- external demoのbrand styleやlayoutを無条件に持ち込んでいない。

Evidence例: repository inventory、candidate record、dependency diff、変更file一覧。

## 2. AI state and interaction

対象UIに関係するstateだけを選び、少なくともhappy path、待機、失敗、回復を確認する。

- idle / empty
- submitting / queued
- streaming / reasoning summary
- tool requested / running / succeeded / failed
- approval required / approved / denied
- cancelled / retry / partial result / completed
- error / network failure

確認項目:

- 同時に矛盾するstateを表示しない。
- user actionとagent / tool actionが区別できる。
- streaming中のlayout shift、scroll jump、focus lossが許容範囲である。
- cancel、retry、approvalの副作用と二重実行を制御する。
- error後も有用なinput、partial result、artifactを保持する。

Evidence例: state fixture、Storybook / test route、component test、event-to-UI mapping。

## 3. Accessibility and input

- semantic HTML、accessible name、heading / landmark構造が適切である。
- keyboardだけでprimary action、disclosure、menu、dialog、approvalを操作できる。
- focus indicatorが見え、open / close / error後のfocus移動が予測可能である。
- statusとerrorを色だけで区別していない。
- live regionがstreaming tokenを過剰読上げせず、重要な状態変化を通知する。
- reduced motionで意味を失わず、不要なloop animationを止める。
- contrast、target size、disabled / busy stateをProject方針に照らして確認する。

Evidence例: keyboard walkthrough、accessibility tree / automated scan、focus screenshot、screen-reader spot check。

## 4. Responsive, theme, and content stress

関連するviewportとthemeで次を確認する。

- mobileでcomposer、approval action、artifact、side panelが画面外へ消えない。
- desktopで過度に疎または密にならず、主要content幅と情報階層が適切である。
- light / dark themeでtoken、contrast、code、statusが破綻しない。
- long text、long code、long URL、unbroken string、table、many citationsを扱える。
- large artifact、many tool calls、long conversationでscrollとperformanceが破綻しない。
- empty、loading、slow network、offline / network failureでlayoutが安定する。

Evidence例: representative viewport screenshot、theme matrix、stress fixture、browser performance observation。

## 5. Implementation quality

- stateとevent payloadがTypeScriptで型付けされ、unsafe castや到達不能stateを増やしていない。
- component境界が再利用可能で、画面固有data取得とpresentational UIが不必要に密結合していない。
- stable keyを使い、streaming updateで大きなsubtreeを再mountしない。
- cleanup、abort、race、out-of-order eventを関係する範囲で扱う。
- dependency、bundle、client boundary、hydration、performanceへの影響を確認する。
- secret、内部prompt、Chain of Thought、sensitive tool input / outputを表示しない。

Evidence例: typecheck、focused unit / component test、dependency / bundle inspection、code review。

## 6. Generated-content and approval security

- AI生成Markdown / HTMLとtool resultがsanitizeまたはsafe rendererを通る。
- URL scheme allowlistがあり、危険なscheme、spoofed destination、unsafe external navigationを拒否する。
- raw HTML、SVG、画像、attachment、artifact previewのactive content、size、origin、sandboxを扱う。
- model生成component / props / eventをruntime schemaとallowlistで制限する。
- approval後のserver-side authorization、scope validation、idempotency、replay protectionを確認する。
- timeout / network failure後に実行状態を照会し、不可逆なside effectを盲目的に再実行しない。
- sanitizer、schema、authorization failureが安全側へ倒れ、errorと回復手段を示す。

Evidence例: malicious Markdown / URL fixture、sanitizer test、schema rejection test、iframe policy、server authorization / idempotency test。

## 7. Localization and visual regression

対象Productが複数localeまたはRTLを支える場合だけ適用する。

- translated label、長い日時 / 数値 / error message、IME、line breakでlayoutとactionが壊れない。
- RTLでreading order、icon direction、tool timeline、code block、composerが意味を失わない。
- visual changeの回帰riskが高い場合、Project既存のvisual regressionを関連stateとviewportへ追加する。

Evidence例: locale / RTL fixture、representative screenshot、既存visual regression結果。

## 8. Project verification

Projectが提供するcommandを優先し、変更範囲に応じて実行する。

1. formatまたはformat check
2. lint
3. typecheck
4. focused unit / component / interaction test
5. related test suite
6. production build
7. browserでのrelevant state確認

全commandを機械的に強制しない。例えばdocumentationだけの依頼にproduction buildは不要だが、実装変更で利用可能なbuildを理由なく省略しない。既存失敗は今回の回帰と分離し、command、exit status、要点を記録する。

## Completion record

最終報告には次を含める。

| Gate | Applicability | Evidence | Result |
| --- | --- | --- | --- |
| Project / source selection | applicable / n/a | files, source, decision | pass / fail / blocked |
| AI states | applicable / n/a | fixtures or tests | pass / fail / blocked |
| Accessibility | applicable / n/a | keyboard / scan | pass / fail / blocked |
| Responsive / theme / stress | applicable / n/a | viewports / fixtures | pass / fail / blocked |
| Generated-content security | applicable / n/a | sanitizer / URL / schema / server checks | pass / fail / blocked |
| Localization / visual regression | applicable / n/a | locale / RTL / screenshots | pass / fail / blocked |
| Type / lint / test / build | applicable / n/a | commands | pass / fail / blocked |

重要な `applicable` gateがfailまたはblockedなら、production-readyと報告しない。安全に残せる成果とblockerを分ける。
