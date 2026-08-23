# Evidence schema v4

`origen-evidence/4`はOrigen Evidence Planeの正本であり、C2PA manifestではない。JSONはduplicate key、NaN、Infinity、unknown critical field、invalid cross-field combinationを拒否する。

## Signature statement

`proof`を除く全objectをdeterministic JSON化したbytesが署名payloadである。digestはSHA-256、signature algorithmはEd25519。

Evidenceは少なくとも次を固定する。

- exact Policy ID/version/mode/digest
- role、signer alias、Provider ID、key ID、signer identity、algorithm
- public keyまたはverifier reference
- Human Root authorization boundary、subject digest、receipt digest
- trusted timestamp Provider、trusted time、receipt digest
- asset/input digest、source map/final snapshot digest、lineage
- builder/Inspector/toolchainとProvider registry digest
- publication representationとtransport metadata

`proof`にはsignature、opaque authorization receipt、opaque timestamp receiptだけを置く。private keyは置かない。

## Human Root

Human Rootは次の境界である。

```text
Human Source -> trusted capture / ingest boundary -> Root Attestation
```

Root Evidenceの`authorization`:

```json
{
  "boundary_type": "trusted_ingest",
  "boundary_id": "capture-service",
  "provider_id": "sign-provider",
  "provider_identity": "capture-and-sign-service",
  "subject_sha256": "...",
  "receipt_digest": "..."
}
```

手動承認の有無は保証の定義ではない。explicit authorizationもpre-authorized workflowも、Providerがsubject-bound receiptを発行し検証できれば同じEvidence modelで表す。AIが任意contentをRoot keyで署名する経路は、authorization receiptなしでは成立しない。

Rootは`publish_ready=false`であり、Root roleは`root-attestor`。trusted timeはexternal timestamp Providerから得る。

## Final Attestation

Finalはvalidationとinspection後に自動署名してよい。roleは`final-attestor`、`authorization=null`。

- STANDARD: AI/external contentを許す。`content_signals.state=unknown`、`no_unmapped_generated_content=false`
- STRICT ORIGIN: `content_basis=signed_human_sources`、source mapping必須、`no_unmapped_generated_content=true`

Root/Finalはlogical roleとして異なるaliasを使う。同一Provider利用は可能。

## Verification and rotation

verifyはEvidenceに固定されたkey ID、algorithm、public verifier referenceを使い、秘密鍵なしで成立する。rotation後も旧Signer aliasとverifier recordをregistryに残す。新しいdefault aliasは新Evidenceだけに使う。

## Schema lifecycle

v4 commandは`origen-evidence/4`と`origen-trust-policy/2`だけを受理する。旧Evidenceを機械的に書き換えない。必要なら現Policy下でRootを再attestし、Finalを再build・inspect・signする。
