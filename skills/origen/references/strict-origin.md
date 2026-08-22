# STRICT ORIGIN source mapping

STRICT ORIGINはAI detectorや見た目の推測ではなく、Final Contentを署名済みHuman sourceへ決定的に追跡する。

## 共通schema

```json
{
  "schema_version": "origen-source-map/1",
  "kind": "text",
  "sources": [
    {
      "source_id": "root",
      "asset": "human.txt",
      "evidence": "human.root.origen.json"
    }
  ]
}
```

pathはsource mapからの相対pathまたはabsolute pathとして実行時だけ使う。signed final evidenceにはpathを保存せず、`source_id`、asset id、evidence digest、operation summaryだけを保存する。

全sourceは次を満たす必要がある。

- regular non-symlink file
- `human-root` evidence
- external verification providerで署名成功
- asset hash / MIME / size一致
- primary `--root-evidence`をsourcesに含む

## Text mapping

```json
{
  "schema_version": "origen-source-map/1",
  "kind": "text",
  "sources": [{"source_id": "root", "asset": "human.md", "evidence": "root.json"}],
  "operations": [
    {"op": "slice", "source_id": "root", "start": 10, "end": 24},
    {"op": "separator", "value": "\n\n"},
    {"op": "slice", "source_id": "root", "start": 0, "end": 9}
  ]
}
```

`start` / `end`はBOM除去、LF変換、NFC後のUnicode code-point indexである。`slice`と固定whitespace separator（empty、space、TAB、LF、blank line）だけを許可する。AIがkeep/delete/move/split/merge/paragraph planを作ることはできるが、新しいliteral wordingは追加できない。

Origenはmapからbytesを再構成し、`finalize`へ提示されたtextのcanonical bytesと完全一致しなければ`STRICT_CONTENT_MISMATCH`で拒否する。v0.2の内蔵Strict textはplain text / Markdownだけを扱う。

## Media mapping

```json
{
  "schema_version": "origen-source-map/1",
  "kind": "media",
  "sources": [{"source_id": "root", "asset": "human.png", "evidence": "root.json"}],
  "primary_source_id": "root",
  "transformation": {"op": "identity"}
}
```

- `identity`: signed Human primary sourceをそのまま形式別Clean Buildへ渡す。内蔵対応はPNG。
- `trusted-deterministic`: verified Human sourcesとparameter objectをexternal adapterへ渡す。adapterは追加Strict保証を返す。

positional inputのhashはprimary sourceと一致する必要がある。AI-generated pixel/waveform/frame/document bytesを入力に差し替えると、adapter起動前に拒否する。

## Verification

STRICT `prepublish`は`--source-map`、`--root-evidence`、`--verify-command`を必須にする。source files/evidence、root inclusion、mapping summaryを再検証し、signed final evidenceと一致する場合だけ`publish_ready=true`を返す。

`content_provenance=verified_clean`はこのsource mapping境界の保証であり、Human sourceそのものに不可知なsteganographic signalが存在しないという保証ではない。
