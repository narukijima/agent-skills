# Trust Policy v1

`origen-trust-policy/1`はProduction authority、toolchain、resource limits、publication representationを固定するversioned JSONである。Policy自身をsecure snapshotし、canonical digestをRoot/Final statementへ署名する。

## Required shape

```json
{
  "schema_version": "origen-trust-policy/1",
  "policy_id": "publisher-production",
  "policy_version": "1.0.0",
  "mode": "production",
  "root_required": true,
  "human_origin_claim": true,
  "allowed_media_types": ["text/plain", "text/markdown", "application/json", "image/png"],
  "approved_signers": {},
  "approved_verifiers": {},
  "approved_builders": {},
  "approved_inspectors": {},
  "approved_timestamp_providers": {},
  "approved_key_ids": [],
  "approved_algorithms": [],
  "creator_key_map": {},
  "resource_limits": {},
  "environment_policy": {},
  "publisher_handoff_policy": {}
}
```

Productionの`root_required` defaultはtrue。rootless STANDARDを許す別profileは`root_required=false`かつ`human_origin_claim=false`でなければならない。

## Approved tool entry

各approved IDはabsolute executable、literal arguments、`expected_executable_sha256`、path-to-hash形式の`expected_script_sha256` / `expected_resource_sha256`を持つ。既存absolute argument fileは必ずどちらかへpinする。version、dependency provenance、reproducible install情報もPolicyへ置く。

signerは追加で`role`、`key_id`、`algorithm`、`signer_identity`を持つ。Production root-attestorは`agent_invocable=false`とし、provider側の人間操作/HSM/KMS/workflow authorization receiptを返す。final-attestorと同じrole/keyとして扱わない。

## Resource limits

実装済みkey:

- `input_file_bytes`, `output_file_bytes`, `decoded_bytes`
- `pixel_count`, `width`, `height`
- `frame_count`, `duration_seconds`, `sample_count`
- `source_count`, `operation_count`, `source_map_bytes`, `json_depth`
- `archive_entry_count`, `compression_ratio`
- `subprocess_timeout_seconds`, `subprocess_stdout_bytes`, `subprocess_stderr_bytes`

Phase 1 built-inはfile/JSON/source map/PNG/ZIP inspection/process limitsを直接強制する。frame/duration/sample等はexternal Inspector coverageで強制し、unknownならrejectする。

## Environment and publication

`environment_policy`はsanitized literal env、approved PATH、`network=deny|explicit`を定義する。Origenはprivate cwd、read-only snapshots、output directory、bounded subprocess I/Oを実装する。OS/container/network sandbox自体は利用側Projectが提供し、そのcontractをPolicy deployment recordへ結び付ける。

`publisher_handoff_policy`はallowed publication representationsとtransport metadataだけを許可する。

## Optional policies

- `slice_boundary_policy`: grapheme/token/word/line/paragraph。code pointはadvanced explicit opt-in。
- `c2pa_policy.action`: `preserve | reissue | detach`。external validationが先。
- `publication_profiles`: Markdown raw HTML/front matter/comment semantics。
- `approved_json_schemas`: hash-pinned `origen-json-shape/1` resource。
