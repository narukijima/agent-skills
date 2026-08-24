---
name: origen
description: Human Sourceをauthorization-bound Rootとして固定し、AI/tool outputを検証・署名してContent Origin / Provenance Evidenceとatomic publish bundleを作る。Signerの秘密鍵保管やPublisher実装には使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.6.0"
  agent-directory.status: "active"
  agent-directory.aliases: "Origen,オリジェン,content-origin,content-provenance"
---

# Origen — Content Origin / Provenance

## 発動条件

- Human SourceをAI・外部toolへ渡す前にRootとして固定するとき
- outputをPublisherへ渡す前にPolicy、lineage、inspection、exact bytesを検証するとき
- STRICT ORIGINでFinalをSigned Human Source mappingから再構築するとき
- background / scheduler / 非対話Runtimeへ移して無人で回すとき

途中のAI利用は禁止しない。秘密鍵保管、password manager、OS key store、KMS/HSM、account、schedule、投稿内容、Publisher実装はOrigen coreの責務ではない。

## 共通境界

```text
Content -> canonicalization / SHA-256 -> sign -> signature + key identity -> verify
```

- 署名標準はEd25519、content digestはSHA-256。
- OrigenはProviderへ `key_id`、`algorithm`、canonical payloadだけを渡す。秘密鍵を受け取らない。
- Provider registryはlocal、OS key store、environment/secret store、password manager、remote signer、cloud KMS、HSM/PKCS#11等を同じprotocolへ接続する。
- configのroot/final/time aliasはsetup後に自動解決する。通常commandでProviderやkey保存先を指定しない。

詳細は[Provider protocol](references/provider-protocol.md)と[Trust Policy / config](references/trust-policy.md)を読む。

## 使用するKnowledge

### Required

- [Evidence v4](references/evidence-schema.md)
- [Trust Policy / config](references/trust-policy.md)
- [Provider protocol](references/provider-protocol.md)

### Conditional

- STRICT ORIGINでは[Strict Origin](references/strict-origin.md)を読む。
- builder / Inspector接続では[Adapters](references/adapters.md)を読む。
- Publisher統合では[publisher handoff](references/publisher-handoff.md)を読む。
- format対応判断では[coverage matrix](references/coverage.md)を読む。
- 無人運用（background / scheduler / 非対話Runtime）では[Unattended Signer Profile](references/unattended.md)を読む。
- security reviewでは[threat model](references/threat-model.md)を読む。
- C2PA / Content Credentialsでは[standards](references/standards.md)を読む。

## Provenance flow

```text
Human Source
  -> secure snapshot
  -> trusted ingest / explicit authorization / pre-authorized workflow /
     trusted capture / hardware-backed or provider authorization
  -> Root signer
  -> Signed Human Root
  -> AI / tools
  -> validation + independent inspection
  -> Final signer
  -> atomic publish bundle
  -> verify / prepublish
  -> Project Publisher
```

Human Rootは毎回の手動クリックではなく、署名対象digestに結び付いたauthorization boundary receiptで保証する。AIが任意contentをRoot keyで署名できる経路は、Providerがauthorizationを発行・再検証できない限り成立させない。

RootとFinalはEvidence上のlogical roleを必ず分ける。異なるaliasを設定し、同一Providerや同一保管基盤を使うことは許す。Finalは自動化してよい。

trusted timeはexternal timestamp Providerから得る。利用者がTime Keyを生成・保管するモデルを使わない。

## 無人運用

無人適格は宣言 → 実測 → 強制の3段で決める。

- 宣言: Provider registry entryの`interaction`が`none` / `per-launch` / `per-signature`。無人適格は`none`だけで、未宣言は不明として拒否する。
- 実測: `setup`と`doctor`が`interaction: none`のProviderへ、秘密鍵を実際に使う`sign`と`verify`をbounded deadline内で要求する。鍵の一覧が即答でも署名だけが止まるProviderがあるので、宣言を信用しない。
- 強制: configの`unattended: true`が、通常commandと旧Evidence検証の両方で未宣言Providerを`UNATTENDED_PROVIDER_REQUIRED`で止める。

password manager agentとOS key storeは承認を元アプリ終了までしか保持しないので無人適格ではない。適格なのはrepository外の`0600`鍵ファイル + SSHSIG、cloud KMS、HSM、remote signerである。`interaction: none`は「そのユーザ権限のprocessから秘密鍵を使える」ことを受け入れる判断であり、漏洩が疑われたらrotationで対応する。reference実装は`providers/unattended-file-signer.py`にあり、Origen coreではない。詳細は[Unattended Signer Profile](references/unattended.md)。

## 最小操作

初回だけProvider registryを選び、health/capabilities、public verifier、Final sign/verify、trusted timeをself-testする。

```bash
python3 skills/origen/scripts/origen.py setup \
  --provider-registry /path/to/providers.json
```

無人で回すdeploymentは`--unattended`を付ける。configへ`unattended: true`を書き、Providerの`interaction: none`宣言を要求し、宣言が本当かをbounded deadlineで実測する。

既定では `.origen/config.json` を作り、次のaliasを保存する。

```json
{
  "root_signer": "default-root",
  "final_signer": "default-final",
  "timestamp_provider": "default",
  "unattended": false
}
```

以後はconfigを自動発見する。schedulerへ投入する前とcredential環境が変わった直後は`doctor`を通す。何も書かず、同じProvider検査を再実行する。

```bash
python3 skills/origen/scripts/origen.py doctor
```

```bash
python3 skills/origen/scripts/origen.py root HUMAN_SOURCE \
  --creator-id CREATOR_ID --origin-id ORIGIN_ID \
  --evidence ROOT.origen.json

python3 skills/origen/scripts/origen.py finalize OUTPUT \
  --root-evidence ROOT.origen.json --bundle publish-bundle

python3 skills/origen/scripts/origen.py verify \
  --bundle publish-bundle --root-evidence ROOT.origen.json

python3 skills/origen/scripts/origen.py prepublish \
  --bundle publish-bundle --root-evidence ROOT.origen.json
```

複数bundleを1回のPublisher handoffで出すときは`prepublish-batch`を使う。singleと同じ署名・Policy・asset・receipt・lineage・trusted time・STRICT rebuild検証をそのまま使い、configごとにPolicyとProvider registryを1回だけsnapshotし、bundle検証だけをbounded concurrencyで走らせる。

```bash
python3 skills/origen/scripts/origen.py prepublish-batch \
  --input prepublish-batch.json --concurrency 4
```

- input schemaは`origen-prepublish-batch/1`、itemは`id`（非空・一意）、`bundle`、`config`、任意の`root_evidence` / `parent_evidence` / `source_map`。
- item数は1..10000、concurrencyは1..8（既定4、item数で頭打ち）。
- result schemaは`origen-prepublish-batch-result/1`で、input順の`results`、`item_count`、`config_loads`、実効`concurrency`を返す。
- 1件でも失敗したらbatch全体をfail closedし、`BATCH_VERIFICATION_FAILED`とitem ID / index / 原因codeを返す。partial resultもpartial receiptも返さない。

別configは `--config` または `ORIGEN_CONFIG` で指定できる。通常操作ではsigner ID、verifier ID、timestamp Provider ID、Provider固有CLIを渡さない。

## 保証

- secure snapshot: no-follow open、mutation detection、content-addressed private copy
- Evidence v4: algorithm、key ID、signer identity、public verifier reference、authorization、policy digest、toolchain、lineage、trusted timeを署名固定
- STANDARD: AI-generated contentを許し、未知signalをcleanと主張しない
- STRICT ORIGIN: Signed Human Source mapping外のgenerated contentをFinalへ導入していないことを検証
- Final: trusted build、independent inspection、final signature、atomic no-overwrite bundle
- verify/prepublish: 秘密鍵なしでsignature、Policy、asset、receipt、lineageを再検証
- prepublish-batch: 同一検証をbundle単位で並列化し、全件合格までpublish-readyを出さない
- setup/doctor: `interaction: none`宣言を、秘密鍵を使う署名のbounded実測で検証し、未宣言Providerを無人経路から締め出す
- unsupported、malformed、unknown critical field、incomplete coverage、不一致はfail closed

Evidence詳細は[Evidence v4](references/evidence-schema.md)、STRICTは[Strict Origin](references/strict-origin.md)、formatは[coverage matrix](references/coverage.md)と[Adapters](references/adapters.md)を読む。

## Publisher境界

OrigenはPublisherを含めない。Publisherはverified bundle内の `asset` だけをstreamし、upload時にSHA-256を再確認し、prepublish後にrewrite/re-encodeしない。batch handoffでは全receiptが揃うまでstagingを開始しない。詳細は[publisher handoff](references/publisher-handoff.md)。

## 秘密鍵・recovery

- private key、secret、credentialをrepository、Evidence、stdout、argvへ出さない。
- generate/import/reference existing key、backup、restore、rotationはProvider capabilityである。
- Origen setupはProviderのpublic verifier情報とself-testだけを記録する。
- rotation後も旧Evidenceを検証できるよう、旧alias、旧key ID、旧verifier recordをregistryから削除しない。
- signer aliasとkey identityはTrust Policy digestへ入らない。signer rotationは未publishのHuman Rootをfinalize不能にしない。
- Trust Policy自体を変えると旧Policy下のbacklogはfinalizeできなくなる。変更前に出し切るか、保管したHuman Sourceへ新Policy下で`root`を実行し直す。再rootは新しいtrusted timeになるので、旧Root Evidenceを残して時刻差を隠さない。

## 配布元での検証

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests
```

利用側へは配布元のexact commitからimportし、`agents/upstream.yaml`のsource、commit、versionを保持する。自動同期、Project state複製、Publisher同梱はしない。

## 禁止事項

- Provider固有のKeychain/password manager/KMS/HSM実装をOrigen coreへ入れない。`providers/`のreference signerはdeployment側の例であり、自動選択しない。
- 無人適格をProviderの自己申告だけで認めない。実測conformance checkを通す。
- 独自PKI、独自CA、独自timestamp authorityを実装しない。
- authorization receiptなしでHuman Rootを作らない。
- AI由来をHuman由来として表現しない。
- adapter自己申告だけでpublish-readyにしない。
- `not_detected`や`unknown`をcleanと呼ばない。
- 有効なC2PAを無検証で削除しない。
