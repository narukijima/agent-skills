# Publisher handoff

`finalize`は次をunique temporary directoryで完成・fsyncし、atomic no-replace directory renameする。

```text
publish-bundle/
  asset
  evidence.json
  receipt.json
```

receipt fields:

- `asset_sha256`, `evidence_digest`, `policy_digest`
- `guarantee_mode`, `media_type`, `publication_representation`
- `allowed_transport_metadata`

`prepublish`はbundle entryをno-follow snapshotし、exact entry set、Evidence signature/Policy/lineage、public verifier、authorization/trusted-time receipt、receipt binding、Final hash、STRICT rebuilt digestを検証し、verified receiptをstdoutへ返す。boolだけをpublication authorizationとして返さない。

Publisher contract:

1. arbitrary asset pathを受けず、verified bundle内`asset`だけを開く。
2. upload streamをSHA-256で再hashしreceiptと比較する。
3. prepublish後にrender/rewrite/re-encodeしない。
4. transport metadataはreceipt allowlistだけを付与する。
5. platform receipt/public URL/readbackはProject Publisherが所有する。

OrigenはSNS/Web/Newsletter Publisher、account、credential、scheduleを含まない。Platform側が後から行うre-encodeや判定は保証対象外。
