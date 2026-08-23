# Config and Trust Policy v2

Origenはdeployment-local Provider registryと、署名対象になるTrust Policyを分離する。

## Config

`.origen/config.json`のschemaは`origen-config/1`である。通常操作はcwdの同path、`ORIGEN_CONFIG`、または`--config`から自動解決する。

```json
{
  "schema_version": "origen-config/1",
  "root_signer": "default-root",
  "final_signer": "default-final",
  "timestamp_provider": "default",
  "provider_registry": "providers.json",
  "policy": {}
}
```

root/final aliasはlogical role分離のため異なる値にする。同一Providerや同じ保管基盤を参照してよい。Provider registry pathはdeployment情報でありPolicy digestへ含めない。

## Trust Policy

`policy`は必要な差分だけを持つ。Origenはdefaultを展開し、aliasと合わせて`origen-trust-policy/2` documentを作り、そのSHA-256 digestをEvidenceへ署名する。

default:

- `policy_id=origen-default`
- `policy_version=1.0.0`
- `mode=production`
- `root_required=true`
- `human_origin_claim=true`
- TXT、Markdown、JSON、PNG
- canonical bytes publication
- network deny contract
- conservative STRICT slice boundaries
- Markdown safe profile
- resource limits

Project固有差分だけを`policy`へ置く。algorithm、key ID、Provider capability、executable、public verifierを重複記述しない。それらはProvider registryから解決し、実際に使った値をEvidenceへ固定する。

## Provider registry

`origen-provider-registry/1`は次を分離する。

```json
{
  "schema_version": "origen-provider-registry/1",
  "providers": {},
  "signers": {},
  "timestamp_providers": {},
  "builders": {},
  "inspectors": {}
}
```

Provider entryはprocess transportとdeployment hardeningを持つ。Signer aliasは`provider`、`key_id`、`algorithm=Ed25519`、`signer_identity`、`verifier`を持つ。Root aliasだけが`root_authorization.accepted_boundaries`を持つ。

Registryに秘密鍵、secret value、credentialを置かない。secret storeの参照や環境変数名はProvider adapterの設定であり、値そのものではない。

## Resource and publication overrides

必要時だけ次をoverrideする。

- `allowed_media_types`
- `resource_limits`
- `environment_policy`
- `publisher_handoff_policy`
- `slice_boundary_policy`
- `c2pa_policy`
- `publication_profiles`
- `approved_json_schemas`
- `external_manifest_policy`

unknown critical field、invalid cross-field state、別Policy digestはfail closedする。Productionでrootless STANDARDを許す場合だけ`root_required=false`かつ`human_origin_claim=false`を明示する。
