# Evidence schema

Origen 0.2はasset本体へ独自metadataを埋め込まず、`origen-evidence/2` JSON sidecarをEvidence Planeの正本にする。これはC2PA manifestではない。

## Provenanceの2層

- `structural_provenance`: `clean | detected | unknown`
  container、metadata、manifest、document property、hidden/active field等、形式adapterが構造として検査できる範囲。
- `content_provenance`: `verified_clean | detected | unknown`
  pixel、waveform、frame、token等のcontent dataそのもののorigin状態。

`structural_provenance = clean` は `content_provenance = verified_clean` を意味しない。re-encode、UTF-8保存、metadata除去だけでContent-Level信号の不存在を主張しない。

STRICTの`verified_clean`は「署名済みHuman source mapping外の未知/AI-generated contentをFinalへ追加していない」ことを示す。Human Root自身にsteganographyやwatermarkが絶対にないという万能なforensic保証ではない。

## 共通構造

```json
{
  "schema_version": "origen-evidence/2",
  "evidence_type": "human-root | final-asset",
  "created_at": "RFC3339 UTC",
  "asset": {
    "id": "sha256:<hex>",
    "sha256": "<hex>",
    "size": 123,
    "media_type": "IANA media type"
  },
  "publish_ready": false,
  "proof": {
    "provider": "provider identifier",
    "key_id": "non-secret key identifier",
    "algorithm": "provider algorithm identifier",
    "signature": "provider-encoded signature"
  }
}
```

`proof`を除くobjectをUTF-8、sorted key、余分な空白なしでserializeしたbytesが署名payloadである。

## Guarantee decision

Final evidenceは次を必須にする。

```json
{
  "guarantee": {
    "level": "standard | strict_origin",
    "structural_provenance": "clean",
    "content_provenance": "unknown | verified_clean",
    "root_verified": true
  },
  "publish_ready": true
}
```

- STANDARD: Structural Clean必須。Content-Levelは常に`unknown`として保持する。Human Rootがない場合、`root_verified=false`を許容する。
- STRICT ORIGIN: Structural Clean、`content_provenance=verified_clean`、`root_verified=true`、signed `source_mapping`をすべて必須にする。
- adapterがContent-Level signalを`detected`と報告した場合、どちらのmodeでも公開拒否する。

## Human Root

`human-root`はcreator/origin identity、asset hash、inspection、timestampを署名する。root captureは公開承認ではないため`publish_ready=false`である。

## Final Assetとlineage

Final evidenceは次を追加する。

- `input_asset`: finalizeへ入ったbytesのhash
- `event.source_kind` / `event.transformations`
- `event.adapter`: 実行adapterと保証
- `inspection`: final再検査結果
- `lineage.root_*` / `lineage.parent_*`: Human Rootとpredecessorへのlink
- `toolchain`: binary/script hash、version、dependency provenance、reproducible install記述
- STRICTのみ`source_mapping`: pathを除いたsigned Human sourceとoperationのportable summary

Evidence digestは署名を含むsidecar JSONのcanonical SHA-256であり、filesystem pathをlineageへ埋め込まない。

## Verification

`verify` / `prepublish`はasset hash、proof、root/parent evidence、guarantee decisionを再検証する。STRICTではsource mapを再読込し、全source evidence、source asset bytes、mapping summaryを再検証する。

`origen-evidence/1`はread/`verify`互換を維持するが、Structural/Content保証を分離していないため、v2 `prepublish`では`EVIDENCE_UPGRADE_REQUIRED`として拒否する。
