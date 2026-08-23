# Evidence schema v3

`origen-evidence/3`はProduction Evidence Planeの正本であり、C2PA manifestではない。JSONはduplicate key、NaN、Infinity、unknown critical field、invalid cross-field combinationをstrict parserで拒否する。

## 分離する4概念

```json
{
  "assurance": {
    "structural": {"state": "clean", "coverage": {}, "inspector_id": "..."},
    "content_signals": {"state": "unknown", "checks": []},
    "derivation": {
      "mode": "standard | strict_origin",
      "no_unmapped_generated_content": false,
      "source_map_digest": null,
      "final_snapshot_digest": "...",
      "operation_schema_version": "origen-operation/1"
    },
    "root": {"verified": true, "assurance_level": "trusted_time"}
  }
}
```

- structural provenance: container、metadata、active content等、Inspector coverage内の構造状態。
- content signal detection: provider watermark等のcheck。`not_detected`はcheck結果でありcleanではない。STANDARD aggregateは常に`unknown`。
- content derivation: STANDARDかSigned Human Source mappingからのSTRICT再構築か。
- root assurance: `signed_assertion | trusted_time | capture_attested`。Phase 1 Productionは`trusted_time`までを必須にする。

## 署名statement

`proof`を除く全objectをdeterministic JSON化したbytesが署名payloadである。statementには少なくとも次を含む。

- exact Policy ID/version/mode/digest
- expected signer/verifier identity、role、key ID、algorithm
- builder/inspector identity
- executable/script/resource hashes、dependency provenance、Python/Unicode/NFC record
- timestamp receipt digest（Root）、Root receipt digest link（Final）
- source map digest、rebuilt output digest、final snapshot digest
- `origen-operation/1`
- publication representation、allowed transport metadata
- root/parent lineage

signature bytesとprovider-issued timestamp receipt本体だけを`proof`へ置く。toolchain、Policy、identity等のtrust claimを`proof`だけに置かない。

## Human Root

Human Rootはcreator ID、origin ID、asset hash/media type、Policy digest、signer key/identity、local claimed time、trusted time、timestamp receipt digest、root assurance levelを署名する。`--timestamp`はlocal claimでありtrusted timeではない。Productionはcreator/key mapping、root-attestor role、外部authorization receipt、trusted timestamp verificationを要求する。

署名は「指定identityがこのbytesをHuman Rootとしてassertした」ことを示す。生物学的人間が全bytesを作ったこと、Root内部に未知signalがないことは示さない。

## Final Asset

Final evidenceはinput/final snapshot、source kind、instruction/content/builder actors、independent Inspector coverage、lineage、publication contractを署名する。

- STANDARD: AI/external contentを許す。`content_signals.state=unknown`、`no_unmapped_generated_content=false`。
- STRICT ORIGIN: `content_basis=signed_human_sources`、`no_unmapped_generated_content=true`、source mapping必須。

## Schema lifecycle

- current schemaは`origen-evidence/3`だけであり、それ以外はread/verify/prepublishすべてでunsupportedとして拒否する。
- 別Policy digest、development evidenceはProduction prepublish拒否。
- 古いsidecarを機械的に書き換えるmigrationは提供しない。必要なら現Policy下でRootを再attestし、Finalを再build・再inspect・再署名する。
