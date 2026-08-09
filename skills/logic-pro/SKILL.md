---
name: logic-pro
description: Safely inspect and operate Logic Pro on macOS through a compatible MCP or adapter. Use for transport, tracks, instruments, MIDI import, save, and bounce with project binding and verified readback.
status: active
aliases: [logic pro, operate-logic-pro]
version: 0.6.1
---

# logic-pro — Logic Proを安全に操作する

## 目的

このSkillは、AIが決めた一つのLogic Pro操作を、対象Projectへ安全に適用し、Logicの実状態で検証するための共通境界を提供する。作曲、編曲、ミックスの判断は利用側のProjectまたは別Skillが所有し、このSkillはDAW操作だけを所有する。

対応範囲は次のとおり。

- 読み取り: Logic、Project、transport、track、region、内蔵音源、MIDI portの状態
- 変更: play、stop、tempo、position、track選択、内蔵音源設定、ローカルMIDI取込、現在Projectの保存、新規保存、安全なbounce
- 対象外: Logic終了、新規Project作成、track/region削除、録音、既存ファイルへのSave As、任意plugin、任意mixer操作、任意ファイル取込

MCP名やtool名を固定しない。実行時に利用可能なLogic Pro用MCPまたはアダプタのcapabilityを調べ、`references/operation-contract.md` の意味操作へ対応付ける。標準macOS環境には同梱の `scripts/logic_macos_adapter.py` をreference profileとして使える。互換経路がなければ、操作できるふりをせず停止する。

## 使用するKnowledge

### Required

- `references/operation-contract.md`: 意味操作、アダプタ要件、引数契約
- `references/safety-and-verification.md`: 安全ゲート、読戻し、結果分類、fallback規則
- `references/macos-adapter.md`: 同梱reference adapterの導入、対応表、終了code、実行例

### Conditional

- なし。接続先固有のtool仕様は、利用側環境でMCPのschemaまたは導入資料から取得する。

## 最初に行うこと

1. Requiredの3資料を最後まで読む。
2. 利用可能なMCP、ローカルアダプタ、computer-control toolを列挙し、Logic専用の状態読取と意味操作があるか確認する。互換MCPがなければ、macOSでは同梱adapterの `capabilities` とfreshなruntime capabilityを確認する。
3. Logicの起動、画面ロック、Accessibility権限、モーダルダイアログ、現在Projectを読み取る。
4. 利用者の依頼から、変更してよい範囲と期待する完全な `.logicx` パスを確定する。推測で別Projectを選ばない。

## 操作ワークフロー

変更操作は必ず一回につき一操作とし、次の順序を崩さない。

1. **Observe**: 操作直前のLogic状態を読む。キャッシュ、default値、前回の観測を使わない。
2. **Bind**: 完全なwindow集合で期待する `.logicx` と現在Projectを一意に一致確認する。file URLを取得できない場合だけ、完全な `.logicx` 名とLogicのProject表示名の厳密一致を代替証拠にする。複数Project候補または不完全なidentity探索では変更しない。
3. **Guard**: 操作要求をJSONにし、`scripts/logic_guard.py preflight` でallowlist、権限、引数、path、Project境界を検査する。
4. **Act**: MCPまたは安全なローカルアダプタへ意味操作を一度だけ送る。複数操作を一つのtool callへまとめない。
5. **Read back**: 操作応答とは別に、Logicの状態を新しく読み、期待状態と比較する。bounceなど出力を作る操作ではartifactも観測する。
6. **Classify**: preflight出力をそのまま結果JSONへ含め、`scripts/logic_guard.py classify` で同じ要求のfingerprint、Logic読戻し、必要なartifactをA/B/Cへ判定する。Aだけを成功として次へ進める。
7. **Report**: 操作、対象Project、観測前後、証拠source、分類を返す。操作成功と音楽作品の完成を混同しない。

preflightの例:

```bash
python3 skills/logic-pro/scripts/logic_guard.py preflight --request request.json
```

結果分類の例:

```bash
python3 skills/logic-pro/scripts/logic_guard.py classify --result result.json
```

JSON形式は `references/operation-contract.md` を使う。ガードが拒否した要求を、引数名だけ変えて迂回しない。

同梱macOS adapterの確認とdispatch:

```bash
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty capabilities
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty observe --operation app.status
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty dispatch --preflight preflight.json
python3 skills/logic-pro/scripts/logic_macos_adapter.py --pretty observe --operation transport.state
```

adapterのdispatch応答をreadbackとして使わない。必ず別の `observe` callを行い、期待値との比較をclassify入力へ入れる。対応範囲とmacOS権限は `references/macos-adapter.md` を使う。

## GUI fallback

MCPを第一経路にする。結果不明（B）のときは再送もGUI fallbackも行わず、状態を再観測して停止する。失敗確定（C）で、利用者の依頼範囲内かつ対象UI要素と読戻し方法が確認できる場合だけ、AccessibilityベースのGUI操作を一回だけ許可する。座標クリックを恒常的な実装にしない。

## 出力契約

- **A / verified_success**: MCPの成功だけでなく、freshなLogic状態が期待値と一致した。成功として返してよい。
- **B / unknown**: 送信、UI、通信、読取、証拠のどれかが曖昧。再送せず、最終観測と不明点を返す。
- **C / confirmed_failure**: 未対応、明示拒否、前提違反など失敗が確定した。原因と安全な次の選択肢を返す。
- 読み取り結果には取得時刻、対象Project、sourceを付ける。未検証のdefault値をLogicの実状態として返さない。

## 禁止事項

- 結果不明の操作を自動再送しない。
- 間違ったProject、ロック中、権限不明、モーダル表示中に変更しない。
- 複数のProject候補、複数のtransport control window、同じ意味操作に一致する複数controlのどれかがある場合は変更しない。
- allowlist外の操作を汎用GUI、AppleScript、CGEvent、MIDI送信で迂回しない。
- 相対path、許可root外のpath、既存出力への上書きを許可しない。
- MCP、adapter、patch、実行ファイルの版やhashが要求される環境では、照合できない版を実行しない。
- 音楽的品質、聴感、権利、bounce内容を、UI操作の成功だけから保証しない。
