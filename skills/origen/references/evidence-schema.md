# Evidence schema

Origen 0.1はasset本体へ独自metadataを埋め込まず、JSON sidecarをEvidence Planeの正本にする。これはC2PA manifestではない。

## 共通構造

```json
{
  "schema_version": "origen-evidence/1",
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

`proof` を除くJSON objectをUTF-8、key sort、余分な空白なしでserializeしたbytesが署名payloadである。値はJSONのstring、integer、boolean、null、array、objectだけに制限する。

## Human Root

`evidence_type = human-root` は次を追加する。

- `origin.creator_id`: 人間または組織を指す安定した非秘密identifier
- `origin.origin_id`: source record、capture、commission等を指す安定したidentifier
- `event.action = human-root-captured`
- `publish_ready = false`: root captureは公開承認ではない

署名は「このidentity assertionとasset hashをsignerが固定した」ことを示す。本人性や権利を自動証明するものではないため、Project側のidentity/rights policyは別に維持する。

## Final Asset

`evidence_type = final-asset` は次を追加する。

- `input_asset`: untrusted inputのhash、size、media type
- `event.source_kind`: `ai-output` / `external-tool` / `human-edit` / `captured-original`
- `event.transformations`: 空でない処理説明のlist
- `event.adapter`: 実際にClean Buildしたtoolとversion
- `inspection`: final assetの再検査結果
- `lineage.root_*`: signed Human Rootがある場合のasset idとevidence digest
- `lineage.parent_*`: 直前のsigned predecessorを主張する場合のasset idとevidence digest
- `publish_ready = true`: finalize時点で全gateが成功した場合だけ設定

Evidence digestは署名を含むsidecar JSONをcanonical serializeしてSHA-256した値である。pathやfilesystem locationをlineageへ埋め込まないので、assetとevidenceを移動してもlinkは維持される。

parent linkはsignerによる派生関係のassertionであり、content similarityを自動証明するものではない。実際にfinalizeへ入ったuntrusted bytesは別の `input_asset` へ固定するため、AI編集後のinputとその上流parentを混同しない。

## Verification

`verify` / `prepublish` は最低限次を再計算する。

1. schemaと必須field
2. asset bytesのSHA-256、size、media type
3. `proof` を除いたpayloadの外部署名
4. root/parent evidence digestとasset id
5. linked evidence自体の署名
6. `prepublish` では `evidence_type = final-asset` と `publish_ready = true`

root/parent referenceが記録されているのに対応evidenceが渡されない場合、chainは `unknown` なのでfail-closedにする。
