# External provider protocol v3

Productionではcommand文字列をCLIへ渡さない。Policyのapproved IDからabsolute executable、literal argv、hash、identityを解決し、実行前に照合する。shellは使わない。

共通実行境界:

- sanitized environment / approved PATH
- private working directory
- read-only content-addressed input snapshots
- dedicated output directory
- Policy timeout / stdout / stderr cap
- network denyまたはexplicit Policy（実network sandboxはProject contract）

## Sign / verify

Rootは先に`authorize_root`を呼び、外部workflowが返すauthorization receipt digestをRoot statementへ入れる。続くsign requestはcanonical payloadとdigest、expected signer role/key/identityだけを含む。Root request scopeは`sign-root-attestor-evidence-v3`、Finalは`sign-final-attestor-evidence-v3`。root-attestorは署名時にstatement内receipt digestをechoし、一致しなければ拒否する。

verifyはpayload、proof、signed expected signerを受け、signature validityとkey/algorithm/identityを返す。secret/private keyをstdin/stdout/argv/repositoryへ置かない。

## Trusted time

`origen-trusted-time/1`はRFC 3161 TSAまたは同等provider-issued receiptへ接続する。

- `timestamp`: subject SHA-256を受け、trusted time、provider identity、protocol、receiptを返す。
- `verify_timestamp`: subject digest、trusted time、receiptを再検証する。

Origenはreceipt digestをRoot statementへ署名し、receipt本体をproofへ置く。任意`--timestamp`をtrusted timeへ昇格しない。

## Build / inspect

Builderはsnapshot path、input digest/media type、output directory、typed operation/source bindingsを受ける。InspectorはFinal snapshot、required coverage、STRICT summaryを受ける。Inspectorはcoverage全項目とcontent signal checksを返す。自己申告だけでなく、approved/pinned独立Inspectorであることがtrust boundaryである。

External toolのbinary/version/script/config/resource hashes、dependency provenance、reproducible installはPolicyとsigned toolchain claimへ残す。
