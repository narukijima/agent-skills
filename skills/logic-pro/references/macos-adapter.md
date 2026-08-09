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

`scripts/logic_macos_adapter.py` は、このSkillに同梱する標準macOS Accessibilityアダプタである。外部package、座標、画像一致、Space keyのtoggleに依存せず、対応するLogic ProプロセスのAccessibility treeから状態と対象controlを特定する。

対応bundle identifierは `com.apple.mobilelogic`（Logic Pro 12系）と `com.apple.logic10`（従来版）である。静的capabilityとfresh observationへ対応一覧および実際に検出したidentifierを含める。observeで選んだidentifierはdispatch直前にも固定し、別のLogicプロセスへ切り替えない。

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

`capabilities` は静的な実装表と `supported_bundle_identifiers`、`observe` の `data.capabilities` はその時点で実測できた操作一覧、`data.bundle_identifier` は実際に選んだLogic Proプロセスを返す。Logic未起動、対応identifierのプロセス不在、権限なし、Project windowなし、transport状態不明の場合、runtime一覧は安全側に縮小する。

## 4. 対応表

reference profile `logic-pro-macos-accessibility` version `0.1.1` の対応は次のとおり。

| 意味操作 | 対応 | 操作または独立読戻し |
| --- | --- | --- |
| `app.status` | 対応 | Logic起動、unlock、Accessibility、modal、frontmost、runtime capability |
| `project.current` | 対応 | main windowの`AXDocument`。取得不能時はwindow titleを明示 |
| `transport.state` | 対応 | Play controlのvalue、またはStop/Go to Beginning controlの排他的表示 |
| `transport.play` | 対応 | 状態を再確認し、Play controlへ`AXPress`を一回 |
| `transport.stop` | 対応 | 状態を再確認し、Stop controlへ`AXPress`を一回 |
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

`dispatch` は次を操作直前に再確認する。

1. preflightのauthorizationとfingerprint
2. Logic起動
3. screen unlock
4. Accessibility権限
5. Logic所有modalの不在
6. `AXDocument`または厳密なwindow titleによるProject binding
7. 操作と `transport.state` のruntime capability

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

`osascript`がdispatch中にtimeoutまたは不正応答になった場合、操作が届いた可能性を捨てず`unknown`にする。未対応operationはUIへ触れる前に`failed`へする。

## 7. 互換性確認

version名だけで互換を推測せず、更新後は実機で次をsmoke testする。

1. `capabilities` が全意味操作を `implemented` または `not-implemented` と理由付きで列挙する。
2. `app.status` が現在のunlock、Accessibility、modalを返す。
3. Logic Pro 12系では `bundle_identifier` が `com.apple.mobilelogic`、従来版では `com.apple.logic10` になる。
4. `project.current` のpath、またはfallback window titleが対象Projectと一致する。
5. 停止中の `transport.state.data.is_playing` がfalse、再生中がtrueになる。
6. `transport.play` と `transport.stop` を別々のpreflightで一回ずつ実行し、それぞれ別processの `observe` で読戻せる。
7. modal表示中、lock中、別Project、Control Bar非表示でdispatchが行われない。
8. timeoutを短くしたfixtureで終了code 3とunknownが維持される。

repository testはLogicを動かさず、AX snapshot fixtureを使って全allowlistのsupport表、日英label、Project binding、fingerprint、timeout区別、独立readback境界を検証する。実機smokeはmacOS TCCとLogic UI状態を変更するため、利用者の明示的な実行環境で行う。

## 8. 設計上の境界

- Project固有root、MIDI/bounce出力policy、音楽的品質規則をadapterへ埋め込まない。
- coordinate click、画像認識、Space toggle、任意key sequenceを使わない。
- `AXPress`対象はLogic所有のmain window内、日英の意味label、対応actionの三条件で特定する。
- 表示layoutやUI言語が互換表外ならcapabilityを返さず停止する。
- adapterの成功は作品完成を意味しない。必ずguardの独立読戻し分類を通す。
