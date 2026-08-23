---
name: origen
description: Human Sourceをauthorization-bound Rootとして固定し、AI/tool outputを検証・署名してContent Origin / Provenance Evidenceとatomic publish bundleを作る。Signerの秘密鍵保管やPublisher実装には使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.4.0"
  agent-directory.status: "active"
  agent-directory.aliases: "Origen,オリジェン,content-origin,content-provenance"
---

# Origen — Content Origin / Provenance

## 発動条件

- Human SourceをAI・外部toolへ渡す前にRootとして固定するとき
- outputをPublisherへ渡す前にPolicy、lineage、inspection、exact bytesを検証するとき
- STRICT ORIGINでFinalをSigned Human Source mappingから再構築するとき

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

## 最小操作

初回だけProvider registryを選び、health/capabilities、public verifier、Final sign/verify、trusted timeをself-testする。

```bash
python3 skills/origen/scripts/origen.py setup \
  --provider-registry /path/to/providers.json
```

既定では `.origen/config.json` を作り、次のaliasを保存する。

```json
{
  "root_signer": "default-root",
  "final_signer": "default-final",
  "timestamp_provider": "default"
}
```

以後はconfigを自動発見する。

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

別configは `--config` または `ORIGEN_CONFIG` で指定できる。通常操作ではsigner ID、verifier ID、timestamp Provider ID、Provider固有CLIを渡さない。

## 保証

- secure snapshot: no-follow open、mutation detection、content-addressed private copy
- Evidence v4: algorithm、key ID、signer identity、public verifier reference、authorization、policy digest、toolchain、lineage、trusted timeを署名固定
- STANDARD: AI-generated contentを許し、未知signalをcleanと主張しない
- STRICT ORIGIN: Signed Human Source mapping外のgenerated contentをFinalへ導入していないことを検証
- Final: trusted build、independent inspection、final signature、atomic no-overwrite bundle
- verify/prepublish: 秘密鍵なしでsignature、Policy、asset、receipt、lineageを再検証
- unsupported、malformed、unknown critical field、incomplete coverage、不一致はfail closed

Evidence詳細は[Evidence v4](references/evidence-schema.md)、STRICTは[Strict Origin](references/strict-origin.md)、formatは[coverage matrix](references/coverage.md)と[Adapters](references/adapters.md)を読む。

## Publisher境界

OrigenはPublisherを含めない。Publisherはverified bundle内の `asset` だけをstreamし、upload時にSHA-256を再確認し、prepublish後にrewrite/re-encodeしない。詳細は[publisher handoff](references/publisher-handoff.md)。

## 秘密鍵・recovery

- private key、secret、credentialをrepository、Evidence、stdout、argvへ出さない。
- generate/import/reference existing key、backup、restore、rotationはProvider capabilityである。
- Origen setupはProviderのpublic verifier情報とself-testだけを記録する。
- rotation後も旧Evidenceを検証できるよう、旧key IDとverifier recordをregistryから削除しない。

## 配布元での検証

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests
```

利用側へは配布元のexact commitからimportし、`agents/upstream.yaml`のsource、commit、versionを保持する。自動同期、Project state複製、Publisher同梱はしない。

## 禁止事項

- Provider固有のKeychain/password manager/KMS/HSM実装をOrigen coreへ入れない。
- 独自PKI、独自CA、独自timestamp authorityを実装しない。
- authorization receiptなしでHuman Rootを作らない。
- AI由来をHuman由来として表現しない。
- adapter自己申告だけでpublish-readyにしない。
- `not_detected`や`unknown`をcleanと呼ばない。
- 有効なC2PAを無検証で削除しない。
