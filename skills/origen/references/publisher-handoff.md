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

## Batch handoff

`prepublish-batch`は`origen-prepublish-batch/1` inputを受け、`id` / `bundle` / `config`と任意の`root_evidence` / `parent_evidence` / `source_map`を持つ1..10000 itemを検証する。

- 同一configのPolicy、Provider registry、limitsはconfig単位で1回だけsnapshotし、`config_loads`で報告する。
- bundle検証だけを1..8のbounded concurrency（既定4）で実行する。item数がconcurrencyより小さいときは実効値まで下げ、`concurrency`で報告する。
- item検証はsingle `prepublish`と同一のsignature、Policy、asset、receipt、lineage、trusted time、STRICT rebuild検証を使う。itemごとに独立したsecure snapshot storeを使い、検証状態を共有しない。
- 結果は`origen-prepublish-batch-result/1`で、input順の`results`（`index`、`id`、single prepublishと同一の`receipt`）を返す。
- 1件でも失敗したらbatch全体を`BATCH_VERIFICATION_FAILED`でfail closeし、`failures`にindex順のitem ID / index / 原因code / messageを返す。partial resultを返さない。
- 失敗集合を決定的にするため全itemを最後まで検証する。configが読めない場合は、そのconfigを参照する全itemの失敗として報告し、bundle検証を開始しない。

Publisher contract:

1. arbitrary asset pathを受けず、verified bundle内`asset`だけを開く。
2. upload streamをSHA-256で再hashしreceiptと比較する。
3. prepublish後にrender/rewrite/re-encodeしない。
4. transport metadataはreceipt allowlistだけを付与する。
5. platform receipt/public URL/readbackはProject Publisherが所有する。
6. batchでは全itemのverified receiptが揃うまでstagingもuploadも開始しない。

OrigenはSNS/Web/Newsletter Publisher、account、credential、scheduleを含まない。Platform側が後から行うre-encodeや判定は保証対象外。
