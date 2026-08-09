# Reference macOS adapter

## 目次

1. 位置づけ
2. 必要条件
3. 導入と確認
4. 対応表
5. 一操作の実行
6. 応答と終了code
7. 互換性確認
8. 設計上の境界

## 1. 位置づけ

`scripts/logic_macos_adapter.py` は、このSkillに同梱する標準macOS Accessibilityアダプタである。外部package、座標、画像一致、Space keyのtoggleに依存せず、対応するLogic ProプロセスのAccessibility treeから状態と対象controlを特定する。Control Barのtransport controlは、現行Logic Proで使われる `AXCheckBox` と従来layoutの `AXButton` を同じ意味境界で扱う。

対応bundle identifierは `com.apple.mobilelogic`（Logic Pro 12系）と `com.apple.logic10`（従来版）である。静的capabilityとfresh observationへ対応一覧および実際に検出したidentifierを含める。observeで選んだidentifierはdispatch直前にも固定し、別のLogicプロセスへ切り替えない。

標準経路はLogicのPIDを再取得して [`AXUIElementCreateApplication`](https://developer.apple.com/documentation/applicationservices/1459374-axuielementcreateapplication) で対象processへ直接接続する。window集合はapplication elementの`AXWindows`を型検証付きのarray valueとして最大64件まで取得し、`AXMainWindow`と照合する。各要素の`AXRole`が`AXWindow`であることを確認し、application elementや別roleの混入を完全なwindow集合として受理しない。この構造検証に失敗した場合はnative descendant/control探索を開始せず、具体的なdiagnosticを残してboundedなSystem Events経路を試す。この経路はLogicをfrontmostへせず、backgroundのLogic 12でもSystem Eventsのwindow wrapperに依存しない。`AXMainWindow`がgenericな`Logic Pro` windowでもProject windowとは決めつけない。window集合全体の`AXDocument`、正確な`.logicx` title、application-level `AXDocument`、`AXTitleUIElement`を比較し、必要な場合は最大512要素・深さ12のdescendant `AXDocument`探索を行う。実際の取得元を`project_identity_source`へ返し、generic application titleだけをProject identityとして扱わない。

直接AX経路が権限・API応答・完全性の条件を満たさない場合だけ、互換用のSystem Events経路へfallbackする。互換経路は `process.windows()`、application-level `AXWindows`属性、直下`uiElements`の`AXWindow` roleの順に完全な集合を探す。Logicがbackgroundで空集合になる場合に限り、元のfrontmost processを保存できるときだけ一度frontmost再試行し、`finally`でfocusを戻す。直接AXの成功時はこのforeground処理を実行しない。macOSの`/usr/bin/python3` 3.9を含む実行環境で動作し、3.10以降専用の`zip(strict=...)`には依存しない。native bridge内部例外は例外種別と内部関数を診断へ残し、transport controlをSystem Eventsで再走査しない。trusted native AXが不完全なcontrol treeを返した場合も、System Eventsで同じcontrol treeを再走査せず、そのnative診断を返してboundedに停止する。ただしnativeの`AXWindows`がapplication elementまたは非window roleを返し、native descendant/control探索を開始していない構造異常では、System Events側でwindow集合とcontrol treeを最初からboundedに検証できる。app/windowの軽量fallbackだけを行う場合は`control_tree_diagnostic`を返す。`AXContents`と`AXChildrenInNavigationOrder`は補助child属性として扱い、ある要素が`illegal_argument`を返しても主要な`AXChildren`探索の成功を無効化しない。dispatchではTCCが正常に未信頼と判定された場合、またはnative action開始前に上記のwindow構造異常を確認した場合だけ互換actionを許し、TCC trust後のtimeout設定失敗ではfallbackしない。互換actionは完全window集合にあるProject document/titleを一意にbindし、全windowを共通4000要素上限内で走査する。native経路と同じく、Play、Stop、先頭移動のいずれかに複数の意味一致controlがあれば状態推定前に`transport_control_ambiguous`で停止し、`AXPress`を行わない。native action経路へ入った後の例外は再送せず、操作済みの可能性がある`unknown`として停止する。

process全体およびwindowの `entireContents()` はLogic 12で無制限に待つ可能性があるため使わない。直接AXのmessaging timeoutは [`AXUIElementSetMessagingTimeout`](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout) でsystem-wide elementと対象applicationへ最大2秒を設定する。Control Barは`AXMainWindow`だけでなく完全なwindow集合を優先順に調べ、全window・全child属性の合計で最大4000要素・深さ32まで幅優先で探索する。各要素ではAppleが定義する [AXChildren](https://developer.apple.com/documentation/applicationservices/kaxchildrenattribute) に加え、[AXContents](https://developer.apple.com/documentation/applicationservices/kaxcontentsattribute) と`AXChildrenInNavigationOrder`を重複排除して辿る。探索上限、主要child属性の取得失敗、または補助child属性のnon-leaf failureは不完全としてtransport capabilityとdispatchを止める。完全なwindow集合が得られない場合、`AXMainWindow` または `AXFocusedWindow` はread-only観測へ使うが、他windowやmodalの不在を証明できないためdispatchしない。Project identity探索も全windowで完了した場合だけbindingへ使用し、異なるdocument/title候補または探索不能部分があれば変更しない。transport候補が複数windowに分かれる場合や完全一致controlが複数ある場合も状態・変更capabilityを公開しない。観測は `window_evidence`、`project_identity_diagnostic`、`project_identity_complete`、`control_windows_scanned`、`control_window_index`、`control_window_title`、`transport_control_window_ambiguous`を含め、ProjectとControl Barが別windowにあるlayoutも説明する。

`app.status` と `project.current` はtransport control treeを走査しない。`transport.state` とdispatch前snapshotだけがwindow集合からcontrolを収集し、`transport_controls_observed`でtree取得の成否、`transport_controls_complete`で選択したtreeまたは全探索が上限内に完了したかを示す。これにより安全ゲートをControl Bar探索から分離し、部分treeの「control不在」を状態証拠に使わない。

このアダプタは意味操作の一部をすぐ使えるreference profileとして提供する。`capabilities` が返さない操作は未対応であり、汎用GUI操作へ自動fallbackしない。他のMCPやアダプタは `operation-contract.md` の同じ意味境界を実装してよい。

## 2. 必要条件

- macOS上で実行する。
- Logic Proを起動し、対象 `.logicx` を開く。
- 実行元のTerminalまたはAgent hostへ、System Settings > Privacy & Security > Accessibilityで明示的に権限を与える。
- 画面をunlockした状態にする。
- Control BarのPlay/Stop controlがAccessibility treeへ公開される標準layoutを使う。
- UI言語は英語または日本語を使う。

Accessibility権限は利用者がmacOSで許可する。アダプタはTCC設定を変更せず、権限を確認できなければ停止する。Appleの権限手順は [Allow accessibility apps to access your Mac](https://support.apple.com/guide/mac-help/-mh43185/mac)、Logic側のAccessibility設定は [Accessibility settings in Logic Pro for Mac](https://support.apple.com/guide/logicpro/lgcpefb6766e/mac) を参照する。

## 3. 導入と確認

別packageのinstallは不要である。Skillをimportした環境で次を実行する。

```bash
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty capabilities
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty observe --operation app.status
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty observe --operation project.current
```

`capabilities` は静的な実装表、`supported_bundle_identifiers`、`supported_transport_control_roles` を返す。`observe` の `data.capabilities` はそのcallで実測できた操作一覧、`data.bundle_identifier` と `data.process_identifier` は実際に選んだLogic Proプロセス、`accessibility_backend` は `AXUIElement` または互換用 `SystemEvents` を返す。`window_discovery_source` と `window_set_complete` はwindow探索証拠、`window_discovery_diagnostic` と `native_accessibility_diagnostic` は直接AXまたはfallbackの原因、`focus_temporarily_changed` は互換経路でのbackground recovery実行有無を返す。Project pathが取れない場合は`current_project_unavailable_reason`と`project_identity_source`で、Projectが存在しない場合と現在layoutがidentityを公開しない場合を区別する。Project選択画面など`.logicx`で終わらないwindow titleはProject identityへ昇格しない。Logic未起動、対応identifierのプロセス不在、権限なし、Project windowなし、transport tree未観測・不完全・状態不明、window集合不完全、または意味labelに一致するcontrolが `AXPress` を持たない場合、runtime一覧は安全側に縮小する。

## 4. 対応表

reference profile `logic-pro-macos-accessibility` version `0.4.2` の対応は次のとおり。

| 意味操作 | 対応 | 操作または独立読戻し |
| --- | --- | --- |
| `app.status` | 対応 | Logic起動、unlock、Accessibility、modal、frontmost、runtime capability |
| `project.current` | 対応 | window集合またはbounded descendantの`AXDocument`。取得不能時は正確な`.logicx` window titleを明示 |
| `transport.state` | 対応 | `AXButton` / `AXCheckBox` Play controlのvalue、またはStop/Go to Beginning controlの排他的表示 |
| `transport.play` | 対応 | 状態を再確認し、完全一致labelと`AXPress`を持つPlay controlへ一回 |
| `transport.stop` | 対応 | 状態を再確認し、完全一致labelと`AXPress`を持つStop controlへ一回 |
| `tracks.list` | 未対応 | 安定ID付きの全track読戻しを標準AX layoutで保証できない |
| `track.selected` | 未対応 | 安定IDまたはindexの独立読戻しを保証できない |
| `regions.list` | 未対応 | region一覧の安定した構造化AX表現を保証できない |
| `instruments.list` | 未対応 | built-in instrument IDの構造化AX表現を保証できない |
| `midi.ports` | 未対応 | CoreMIDI列挙をこの表示アダプタの境界に含めない |
| `transport.set_tempo` | 未対応 | locale/layout非依存のvalue設定と読戻しを保証できない |
| `transport.set_position` | 未対応 | 表示形式非依存のvalue設定と正規化読戻しを保証できない |
| `track.select` | 未対応 | 安定track bindingを保証できない |
| `track.set_instrument` | 未対応 | 複数dialogを跨ぐ安定ID操作と読戻しを保証できない |
| `midi.import_file` | 未対応 | import結果のregion差分読戻しを保証できない |
| `project.save` | 未対応 | dirty stateまたは保存完了の独立読戻しを保証できない |
| `project.save_as` | 未対応 | dialog連鎖と新規Project bindingの独立読戻しを保証できない |
| `project.bounce` | 未対応 | bounce dialog設定と完了の独立読戻しを保証できない |

AppleのLogic ProガイドではPlay/StopをControl Barの状態付きbuttonとして説明し、SpaceをPlay or Stopのtoggleとしている。reference profileはtoggle keyを送らず、意味が確定したbuttonだけへ`AXPress`する。参照: [Play a project in Logic Pro for Mac](https://support.apple.com/guide/logicpro/play-a-project-lgcpcf007560/mac)、[Key commands for Global Commands](https://support.apple.com/guide/logicpro/lgcp02bf31b6/mac)。

## 5. 一操作の実行

adapterへ生の操作要求を渡さない。まずguardのpreflightを通し、その出力全体を渡す。

```bash
python3 skills/logic-pro/scripts/logic_guard.py preflight --request request.json > preflight.json
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty dispatch --preflight preflight.json > dispatch.json
```

`dispatch` はpreflight時に観測したbundle identifierとPIDを固定し、操作直前に同じPIDのLogicが存在することを再確認する。直接AX経路では新しいapplication elementとwindow/control snapshotを作り、次を再確認する。

1. preflightのauthorizationとfingerprint
2. Logic起動
3. screen unlock
4. Accessibility権限
5. Logic所有modalの不在
6. 完全なLogic window集合を取得した証拠
7. `AXDocument`または厳密なwindow titleによるProject binding
8. 操作と `transport.state` のruntime capability

応答に `readback` は含まれない。成功応答を証拠として再利用せず、新しいprocess callで独立読戻しする。

```bash
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty observe --operation transport.state > readback-observation.json
```

呼出側は `data.is_playing` を期待値と比較し、次の形へ正規化して `logic_guard.py classify` に渡す。

```json
{
  "fresh": true,
  "source": "logic-accessibility",
  "matches_expected": true
}
```

`observed_at`、Project、比較に使った実値も最終reportへ残す。

## 6. 応答と終了code

| 終了code | 意味 | dispatch.status | 自動再送 |
| --- | --- | --- | --- |
| `0` | capability、read、またはdispatch応答を正常取得 | `success` | readback後に判断 |
| `2` | 未対応、権限なし、Project違い、引数不正など操作前に確定した失敗 | `failed` / definitive | 禁止。原因を直して新規preflight |
| `3` | timeout、Accessibility bridge異常など操作済みか断定不能 | `unknown` / non-definitive | 禁止。read-onlyで再観測 |

`AXUIElementPerformAction`または互換用`osascript`がdispatch中にtimeoutや応答不能になった場合、操作が届いた可能性を捨てず`unknown`にする。未対応operationはUIへ触れる前に`failed`へする。直接AXのaction一覧は [`AXUIElementCopyActionNames`](https://developer.apple.com/documentation/applicationservices/1462053-axuielementcopyactionnames) で読み、`AXPress`が実際に公開されたcontrolだけを対象にする。

## 7. 互換性確認

version名だけで互換を推測せず、更新後は実機で次をsmoke testする。

1. `capabilities` が全意味操作を `implemented` または `not-implemented` と理由付きで列挙する。
2. `app.status` が現在のunlock、Accessibility、modalを返す。
3. Logic Pro 12系では `bundle_identifier` が `com.apple.mobilelogic`、従来版では `com.apple.logic10` になる。
4. `project.current` のpath、またはfallback window titleが対象Projectと一致する。
5. backgroundのLogic 12で `accessibility_backend` が `AXUIElement`、`window_discovery_source` が `AXUIElement.AXWindows` となり、`focus_temporarily_changed` がfalseのままProject identityと完全window集合を取得できる。
6. `process_identifier`、`native_accessibility_diagnostic`、`window_discovery_source`、`window_discovery_diagnostic`、`window_set_complete` がprocess binding、window取得経路、fallback理由、dispatch可否を説明する。
7. `app.status` と `project.current` の `transport_controls_observed` / `transport_controls_complete` がfalseで、transport control treeを走査しない。
8. 停止中の `transport.state.data.is_playing` がfalse、再生中がtrueになり、両transport control証拠がtrueになる。
9. `AXButton` と `AXCheckBox` の対応layoutで `transport.play` と `transport.stop` を別々のpreflightで一回ずつ実行し、それぞれ別processの `observe` で読戻せる。
10. modal表示中、lock中、window集合不完全、Project identity不完全、control tree上限到達、複数Project/control候補、別Project、Control Bar非表示でdispatchが行われない。
11. macOSの`/usr/bin/python3` 3.9で対象test suiteを実行し、native window選択が3.10以降専用APIへ依存しない。
12. genericな`Logic Pro` main windowとProject/Control Barを持つ別windowのfixtureで、Project identityとtransport stateが別々に回復する。
13. native bridge内部例外のfixtureではSystem Eventsのtransport control走査またはaction再送を呼ばず、観測では診断を返し、native action開始後は`unknown`として停止する。
14. timeoutを短くしたfixtureで終了code 3とunknownが維持される。

repository testはLogicを動かさず、AX snapshot fixtureを使って全allowlistのsupport表、empty-window、完全・不完全window fallback、`AXButton` / `AXCheckBox`、日英label、`AXPress`、Project binding、fingerprint、timeout区別、独立readback境界を検証する。実機smokeはmacOS TCCとLogic UI状態を変更するため、利用者の明示的な実行環境で行う。

## 8. 設計上の境界

- Project固有root、MIDI/bounce出力policy、音楽的品質規則をadapterへ埋め込まない。
- coordinate click、画像認識、Space toggle、任意key sequenceを使わない。
- `AXPress`対象はLogicの同じPIDに属する完全window集合内、正規化後も完全一致する日英の意味labelまたはidentifier、対応actionの三条件で特定する。
- `AXMainWindow` / `AXFocusedWindow`だけのfallbackで変更操作を許可しない。
- 直接AX経路でLogicをforegroundへ変更しない。互換経路のbackground recoveryは元のfrontmost processを特定できる場合だけ一度行い、観測・操作の終了時にfocusを戻す。
- process全体またはwindowの無制限な `entireContents()` を使わない。
- 上限へ達したcontrol treeの不在証拠からtransport状態を推測しない。
- 表示layoutやUI言語が互換表外ならcapabilityを返さず停止する。
- adapterの成功は作品完成を意味しない。必ずguardの独立読戻し分類を通す。
