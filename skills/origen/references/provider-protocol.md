# External provider protocol

Origenは秘密鍵や複雑なmedia encoderを所有しない。commandはshellを介さずargvとして起動し、stdin/stdoutの1 JSON messageで接続する。

## Signing provider

`--sign-command '/trusted/origen-sign-provider --profile human-root'`

stdin:

```json
{
  "operation": "sign",
  "payload": "base64 encoded canonical evidence payload",
  "payload_sha256": "hex"
}
```

stdout:

```json
{
  "provider": "kms:example",
  "key_id": "non-secret stable key id",
  "algorithm": "ES256",
  "signature": "base64 or provider-defined encoded signature"
}
```

Providerは秘密鍵をstdout/stderrへ出さない。KMS/HSM/service内部で署名し、Agentへkey materialを返さない。

## Verification provider

`--verify-command '/trusted/origen-verify-provider --trust-policy publisher-v1'`

stdinはsign requestと同じpayload情報に `proof` を追加する。stdoutは次を返す。

```json
{
  "verified": true,
  "provider": "kms:example",
  "key_id": "non-secret stable key id",
  "algorithm": "ES256"
}
```

Origenはprovider/key/algorithmがsigned proofと一致しない場合もrejectする。certificate chain、revocation、timestamp authority、trust list等のpolicyはprovider側が所有する。

## Trusted rebuild adapter

`--adapter-command '/trusted/origen-media-adapter --policy publisher-v1'`

stdin:

```json
{
  "operation": "rebuild",
  "input_path": "/absolute/untrusted/input",
  "output_path": "/absolute/temporary/output",
  "input_media_type": "image/jpeg",
  "input_family": "image"
}
```

adapterは指定outputへ新規assetを書き、stdoutへ次を返す。

```json
{
  "status": "rebuilt",
  "tool": "organization/tool-name",
  "version": "pinned version",
  "media_type": "image/jpeg",
  "guarantees": [
    "decoded-content",
    "clean-container-rebuild",
    "metadata-policy-applied",
    "provenance-inspected",
    "output-validated"
  ]
}
```

5つのguaranteeはすべて必須。これは文字列を返せば任意toolが安全になるという意味ではない。Projectはcommand path、binary/version、configuration、sandbox、trust policyを別途管理する。Origenはその明示的trust decisionを証拠へ固定する境界である。

final assetへC2PA等を意図的に再発行した場合だけ、`embedded_provenance: "validated-final"` を追加する。これがないoutputでknown provenance markerを検出した場合、Origenはrejectする。

## C2PA provider

C2PA/CAWGを使う場合、適合SDKや`c2patool` wrapperをprovider/adapterに実装し、private key pathをOrigenへ渡さない。C2PA validation result、trust list、certificate identityをprovider側で検証し、Origenには成功/失敗と非秘密identifierだけを返す。
