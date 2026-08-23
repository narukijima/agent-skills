# Provider protocol v1

Origen coreとSigner / Verifier / trusted time実装の間は、JSON requestをstdin、単一JSON responseをstdoutで交換する小さなprocess protocolである。shell commandや秘密鍵をCLIへ渡さない。

Provider transportは`origen-provider-registry/1`へ置き、Trust Policyから分離する。実装はlocal process、OS key store adapter、environment/secret store、password manager、remote signer、cloud KMS、HSM/PKCS#11のいずれでもよい。

## Signer / Verifier

共通request:

```json
{
  "operation": "sign",
  "protocol": "origen-signer/1",
  "role": "root-attestor | final-attestor",
  "key_id": "key:2026-08",
  "algorithm": "Ed25519",
  "payload": "BASE64_CANONICAL_BYTES",
  "payload_sha256": "..."
}
```

`verify`は同じkey ID、algorithm、payload、payload digestに加え、`signature`とEvidenceに固定された`verifier`を受ける。

Providerは次を返す。

- sign: `provider_id`, `key_id`, `algorithm`, `signer_identity`, `signature`
- verify: 上記identityと`verified`
- get_public_key: `key_id`, `algorithm`, `verifier`
- health: `healthy`
- capabilities: supported `operations`

`verifier`は`public_key`または`verifier_ref`を持つ。private keyをrequest/response、stdout、argv、repositoryへ出さない。

## Human Root authorization

Root signerは`sign`の前に`authorize_root`を実行する。

```json
{
  "operation": "authorize_root",
  "protocol": "origen-root-authorization/2",
  "subject_sha256": "...",
  "policy_id": "...",
  "policy_version": "..."
}
```

responseは次を返す。

```json
{
  "boundary_type": "trusted_ingest",
  "boundary_id": "capture-service",
  "subject_sha256": "...",
  "receipt": "OPAQUE_PROVIDER_RECEIPT"
}
```

標準boundary type:

- `trusted_ingest`
- `explicit_authorization`
- `pre_authorized_workflow`
- `trusted_capture_service`
- `hardware_backed_authorization`
- `provider_authorization`

Origenはtype、boundary ID、Provider identity、subject digest、receipt digestをRoot statementへ署名する。receipt本体はproofへ置く。検証時は`verify_authorization`でreceiptを再検証する。

これは「毎回人間がボタンを押した」ことではなく、「Human Sourceとして承認されたProvider boundaryを通過した」ことを保証する。Providerが任意のAI contentへreceiptを発行しないことはProviderのsecurity contractである。

## Trusted time

`origen-trusted-time/1`はRFC 3161 TSAまたは同等の検証可能なexternal timestamp serviceへ接続する。

- `timestamp(subject_sha256)`: trusted time、Provider identity、protocol、opaque receipt
- `verify_timestamp(subject_sha256, trusted_time, receipt)`: `verified`

Origenは独自Time Keyやtimestamp authorityを持たない。local clockはcreated-at claimにすぎず、trusted timeへ昇格しない。

## Registry transport hardening

Process adapterはabsolute executable、literal argv、実行ファイル/script/resource hash、timeout、stdout/stderr上限、sanitized environmentを使う。`inherit_environment`はProviderに必要な既存変数名だけを列挙し、値をregistryへ保存しない。

Builder / Inspectorも同じtransport hardeningを再利用するが、Signer protocolとは別Capabilityである。

## Setup / recovery / rotation

`origen setup`はProvider health/capabilities、public verifier、Final key sign/verify、trusted timeをself-testする。Root keyで任意self-test payloadを署名しない。

generate、import、existing key reference、remote key enrollment、backup、restoreはProvider固有operationまたは運用手順であり、Origen coreは実装しない。rotationでは新aliasをdefaultへ切り替え、旧Evidence検証用の旧alias、key ID、verifier recordを保持する。
