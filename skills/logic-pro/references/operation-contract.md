# Logic Pro operation contract

## 目次

1. 接続先の要件
2. 意味操作
3. preflight入力
4. 操作別引数
5. classify入力
6. 接続先への対応付け

## 1. 接続先の要件

互換MCPまたはアダプタは、少なくとも次を提供する。

- Logicの実状態を構造化データとしてfreshに読む経路
- 一つの意味操作だけを実行する経路
- tool schemaまたは同等のcapability一覧
- timeoutと明示失敗を区別できる応答

下位実装はAccessibility、CGEvent、CoreMIDI/MCU、AppleScript、Logic Scripterなどを利用してよい。ただし、入力を送信できることとLogic状態が変わったことを同一視しない。

このSkillは標準実装として `scripts/logic_macos_adapter.py` を同梱する。macOS Accessibilityだけで安全に意味と独立読戻しを確定できるoperationを実装し、残りは理由付きの`not-implemented`として列挙する。導入、runtime capability、全allowlist対応表、終了codeは `macos-adapter.md` を参照する。

## 2. 意味操作

初期allowlistは次のとおり。接続先のtool名は右辺へ対応付け、左辺の意味を変えない。

| 意味操作 | 種別 | 期待する引数 |
| --- | --- | --- |
| `app.status` | read | なし |
| `project.current` | read | なし |
| `transport.state` | read | なし |
| `tracks.list` | read | なし |
| `track.selected` | read | なし |
| `regions.list` | read | なし |
| `instruments.list` | read | なし |
| `midi.ports` | read | なし |
| `transport.play` | write | なし |
| `transport.stop` | write | なし |
| `transport.set_tempo` | write | `tempo` |
| `transport.set_position` | write | `position` |
| `track.select` | write | `track_id` または `track_index` |
| `track.set_instrument` | write | `track_id`, `instrument_id` |
| `midi.import_file` | write | `path` |
| `project.save` | write | なし |
| `project.save_as` | write | `path` |
| `project.bounce` | write | `path` |

録音、削除、Project新規作成、既存pathへのSave As、任意plugin/mixer操作はこのversionではallowlist外である。

## 3. preflight入力

`scripts/logic_guard.py preflight --request <file>` は次のJSONを受け取る。

```json
{
  "operation": "midi.import_file",
  "arguments": {"path": "/Users/me/Music/input/phrase.mid"},
  "authorization": {"write": true},
  "environment": {
    "logic_running": true,
    "screen_unlocked": true,
    "accessibility_authorized": true,
    "modal_dialog": false,
    "capabilities": ["midi.import_file", "regions.list"],
    "expected_project": "/Users/me/Music/Song.logicx",
    "current_project": "/Users/me/Music/Song.logicx",
    "current_project_unavailable": false,
    "window_project_name": "Song.logicx",
    "available_track_ids": ["track-1"],
    "available_track_indexes": [0],
    "available_instruments": []
  },
  "policy": {
    "allowed_input_roots": ["/Users/me/Music/input"],
    "allowed_output_roots": ["/Users/me/Music/output"]
  }
}
```

`authorization.write` は、利用者の現在の依頼がその変更を含む場合だけtrueにする。preflightはLogicを操作しない。許可時は正規化された`request`、要求fingerprint、必要な`artifact_requirement`を返し、拒否時は理由をJSONで返して終了code 2になる。このpreflight出力を変更せずclassifyへ渡す。

`project.bounce`のpreflight出力例:

```json
{
  "ok": true,
  "classification": "authorized",
  "operation": "project.bounce",
  "impact": "write",
  "request": {
    "operation": "project.bounce",
    "arguments": {"path": "/Users/me/Music/output/mix.wav"},
    "expected_project": "/Users/me/Music/Song.logicx"
  },
  "request_sha256": "...",
  "artifact_requirement": {
    "path": "/Users/me/Music/output/mix.wav",
    "kind": "regular-file",
    "min_size_bytes": 1
  }
}
```

Project scoped read (`transport.state`以降のread) と全writeはProject bindingを要求する。`current_project`を取得できない場合、`current_project_unavailable=true`かつ`window_project_name`が期待Projectのbasenameと完全一致するときだけfallback証拠を認める。

## 4. 操作別引数

- `tempo`: 1より大きく1000以下の有限数値。
- `position`: 接続先が受け取る空でない文字列。接続先のschemaへ変換する前の論理値を記録する。
- `track.select`: 直前にLogicから取得した`available_track_ids`内の`track_id`、または`available_track_indexes`内の0以上の`track_index`のどちらか一つ。
- `track.set_instrument`: `available_track_ids`内の`track_id`と、直前にLogicから取得した`available_instruments`内の`instrument_id`。
- `midi.import_file`: 許可input root内に存在する絶対pathの`.mid`または`.midi`通常ファイル。
- `project.save_as`: 許可output root内にある既存directoryを親に持つ、まだ存在しない絶対`.logicx` path。
- `project.bounce`: 許可output root内にある既存directoryを親に持つ、まだ存在しない絶対path。拡張子は`.wav`, `.aif`, `.aiff`, `.m4a`, `.mp3`。

symlink解決後のpathでroot境界を判定する。既存出力は上書きしない。

## 5. classify入力

変更後、preflight出力、操作応答、独立した読戻しを次の形にする。出力操作では`artifact`も含める。

```json
{
  "preflight": {
    "ok": true,
    "classification": "authorized",
    "operation": "project.bounce",
    "impact": "write",
    "request": {
      "operation": "project.bounce",
      "arguments": {"path": "/Users/me/Music/output/mix.wav"},
      "expected_project": "/Users/me/Music/Song.logicx"
    },
    "request_sha256": "...",
    "artifact_requirement": {
      "path": "/Users/me/Music/output/mix.wav",
      "kind": "regular-file",
      "min_size_bytes": 1
    }
  },
  "dispatch": {"status": "success", "definitive": true},
  "readback": {
    "fresh": true,
    "source": "logic-accessibility",
    "matches_expected": true
  },
  "artifact": {
    "path": "/Users/me/Music/output/mix.wav",
    "observed_after_dispatch": true
  }
}
```

- `preflight`: 実行前にguardが返したJSON全体。fingerprintを再計算して要求との結合を検査する。
- `dispatch.status`: `success`, `failed`, `unknown`
- `dispatch.definitive`: 明示的な未対応、拒否、入力エラーなど失敗が確定した場合だけtrue
- `readback.source`: `logic-accessibility`または`logic-mcp-state`
- `readback.matches_expected`: 操作ごとに定めた期待状態との比較結果
- `artifact.path`: preflightが許可した出力pathと厳密に同じpath
- `artifact.observed_after_dispatch`: dispatch後の観測である場合だけtrue

成功応答だけではAにならない。freshで独立したLogic読戻しが一致し、artifact requirementがある操作ではguard自身のfilesystem検査も通って初めて`verified_success`になる。`project.bounce`はsymlinkでない通常fileの存在と1 byte以上、`project.save_as`はsymlinkでない`.logicx` directoryの存在を要求する。timeout、接続断、stale値、source不明、不一致、artifact不在、zero-byte出力は`unknown`であり、自動再送しない。

## 6. 接続先への対応付け

1. tool schema、または同梱adapterの `capabilities` を読む。
2. 読み取りtoolと変更toolを分ける。
3. 各toolを上記の意味操作へ一対一に対応付ける。
4. toolが複数副作用を持つ場合は使わない。
5. 接続先が返す成功値、timeout、拒否を`dispatch`へ正規化する。
6. 操作toolの返却値をreadbackとして再利用せず、状態読取toolをもう一度呼ぶ。

互換MCPがなくmacOSで実行する場合は、同梱reference adapterの静的対応表とfreshなruntime capabilityを確認する。それでも意味操作または独立読戻しが見つからない場合、一般的なshellやGUIから同等操作を即席で作らず、当該operationが未対応だと報告する。
