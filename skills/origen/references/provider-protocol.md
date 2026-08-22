# External provider protocol

Origenは秘密鍵や複雑なmedia encoderを所有しない。commandはshellを介さずargvとして起動し、stdin/stdoutの1 JSON messageで接続する。

## Signing provider

`--sign-command '/trusted/origen-sign-provider --profile human-root'`

stdin:

```json
{
  "operation": "sign",
  "request_scope": "sign-canonical-evidence-only",
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
  "signature": "base64 or provider-defined encoded signature",
  "provider_version": "pinned provider version",
  "key_protection": "kms | hsm | hardware-backed | external-service",
  "dependency_provenance": "provider build/deployment origin",
  "reproducible_install": "pinned deployment procedure",
  "request_scope": "sign-canonical-evidence-only"
}
```

Providerは秘密鍵をstdout/stderrへ出さない。KMS/HSM/service内部で署名し、Agentへkey materialを返さない。sign requestにはcanonical payloadとdigestしか渡さず、filesystem output path、Publisher credential、任意write操作を渡さない。Provider側もkey ID、caller identity、evidence type等でauthorizeし、`sign ≠ arbitrary agent write permission`を維持する。

長期private key fileやsecret値をcommand line、Agent-readable environment/fileへ置く構成は本番providerとして認めない。test fixtureのdigest signerはprotocol test専用であり、production signatureではない。

## Verification provider

`--verify-command '/trusted/origen-verify-provider --trust-policy publisher-v1'`

stdinはsign requestと同じpayload情報に `proof` を追加する。stdoutは次を返す。

```json
{
  "verified": true,
  "provider": "kms:example",
  "key_id": "non-secret stable key id",
  "algorithm": "ES256",
  "provider_version": "pinned provider version",
  "key_protection": "kms"
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
  "input_family": "image",
  "guarantee_level": "standard"
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
  ],
  "content_provenance": "unknown",
  "dependency_provenance": "package/lock or binary origin",
  "reproducible_install": "pinned installation procedure"
}
```

5つのguaranteeはすべて必須。これは文字列を返せば任意toolが安全になるという意味ではない。Projectはcommand path、binary/version、configuration、sandbox、trust policyを別途管理する。Origenはその明示的trust decisionを証拠へ固定する境界である。

final assetへC2PA等を意図的に再発行した場合だけ、`embedded_provenance: "validated-final"` を追加する。これがないoutputでknown provenance markerを検出した場合、Origenはrejectする。

Origenはadapter commandの解決済みexecutableと、argv中の実在script/config fileをSHA-256でfingerprintし、version、dependency provenance、reproducible install記述とともにfinal evidenceへ固定する。内蔵adapterはPython runtime path/version/hashとOrigen script hashを記録する。

## STRICT ORIGIN adapter

STRICT requestは通常fieldに加えて、検証済みHuman sourceだけを渡す。

```json
{
  "guarantee_level": "strict_origin",
  "strict_origin": {
    "verified_sources": [
      {
        "source_id": "root",
        "asset_path": "/verified/human-source",
        "asset_id": "sha256:...",
        "evidence_digest": "..."
      }
    ],
    "transformation": {"op": "trusted-deterministic", "parameters": {}}
  }
}
```

STRICT adapterは通常の5保証に加え、`human-origin-inputs-only`、`deterministic-transformation`、`content-origin-mapped`を返す。Origenはpositional inputがprimary signed Human sourceと一致することをadapter起動前に確認する。

## C2PA provider

C2PA/CAWGを使う場合、適合SDKや`c2patool` wrapperをprovider/adapterに実装し、private key pathをOrigenへ渡さない。C2PA validation result、trust list、certificate identityをprovider側で検証し、Origenには成功/失敗と非秘密identifierだけを返す。
