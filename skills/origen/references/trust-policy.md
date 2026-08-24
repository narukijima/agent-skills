# Config and Trust Policy v3

Origenはdeployment-local Provider registryと、署名対象になるTrust Policyを分離する。

## Config

`.origen/config.json`のschemaは`origen-config/2`である。通常操作はcwdの同path、`ORIGEN_CONFIG`、または`--config`から自動解決する。

```json
{
  "schema_version": "origen-config/2",
  "root_signer": "default-root",
  "final_signer": "default-final",
  "timestamp_provider": "default",
  "provider_registry": "providers.json",
  "unattended": false,
  "policy": {}
}
```

root/final aliasはlogical role分離のため異なる値にする。同一Providerや同じ保管基盤を参照してよい。

`unattended: true`はbackground / scheduler / 非対話Runtime向けのenforcement switchである。configのroot/final/timestamp aliasと、Evidenceから解決した旧signer / 旧timestamp Providerのすべてに`interaction: none`を要求し、外れたら`UNATTENDED_PROVIDER_REQUIRED`で拒否する。`origen setup --unattended`で書き、`origen doctor`で実測する。[Unattended Signer Profile](unattended.md)を読む。

signer alias、`provider_registry` path、`unattended`はdeployment情報でありPolicy digestへ含めない。

## Trust Policy

`policy`は必要な差分だけを持つ。Origenはdefaultを展開して`origen-trust-policy/3` documentを作り、そのSHA-256 digestをEvidenceへ署名する。

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

## Policy digestに入るもの・入らないもの

| 入る | 入らない |
| --- | --- |
| `policy_id` / `policy_version` / `mode` | root/final/timestamp signer alias |
| `root_required` / `human_origin_claim` | key ID、algorithm、signer identity、public verifier |
| `allowed_media_types`、supplied `resource_limits` | Provider executable、argv、hash pin、`interaction` |
| `environment_policy`、`publisher_handoff_policy` | `provider_registry` path、`unattended` |
| `slice_boundary_policy`、`c2pa_policy` | Provider registry全体 |
| `publication_profiles`、`approved_json_schemas`、`external_manifest_policy` | |

右列はdeployment情報である。実際に使ったsigner aliasとkey identityはEvidenceの`identities.signer`へ署名済みで、検証時にProvider registryの現行entryと完全一致を要求する（`SIGNER_REGISTRY_MISMATCH`）。Policy documentへ重ねて書くと、rotationが常にPolicy変更になってしまう。

## Rotationとfinalize

未publishのContentは、Human Rootを固定した時点のPolicy digestに縛られる。`verify_policy_claim`は署名済みpolicy claimと現行policy claimの完全一致を要求し、外れると`POLICY_DIGEST_MISMATCH`になる。

- **signer rotationはbacklogを取り残さない。** signer aliasもkey identityもPolicy digestへ入らないので、新signerへ切り替えても、rotation以前に固定した未publishのHuman Rootをそのままfinalizeできる。旧alias、旧key ID、旧verifier recordをregistryから削除しないこと（旧Root Evidenceの検証に要る）。
- **Trust Policy自体の変更はbacklogを取り残す。** `policy_version`、`mode`、media type、limits、profile等を変えると、旧Policy下のRoot Evidenceはfinalizeできなくなる。これは仕様どおりの拒否であり、回避してはならない。
  - Trust Policyを変える前に未publishのbacklogを出し切る。
  - 出し切れない場合は、保管しておいたHuman Source assetへ新Policy下で`origen root`を実行し直す。新しいauthorization boundary receiptと新しいtrusted timeが付く。**元の署名時刻は引き継がない**ので、旧Root Evidenceを検証可能なまま保管し、時刻の差を隠さない。
  - `POLICY_DIGEST_MISMATCH`は`expected_policy`と`signed_policy`の両方を返すので、どのfieldが動いたかを`policy_version`とdigestで突き合わせる。

### 0.5.0からのmigration

Policy documentのschemaが`origen-trust-policy/2`から`/3`へ上がり、signer aliasが外れたので、**0.5.0で作ったEvidenceのpolicy digestは0.6.0と一致しない**。0.6.0をimportする前に、0.5.0の未publish backlogを出し切る。出し切れないものは、0.6.0導入後に保管したHuman Sourceへ`root`を実行し直す。以後のsigner rotationでは、この一度きりの断絶は起きない。
