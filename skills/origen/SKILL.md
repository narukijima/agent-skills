---
name: origen
description: Human Rootをtrusted timestamp付きで署名固定し、Policy-pinned build・独立inspection・atomic bundleによってContent Origin / ProvenanceをProduction publication境界で検証する。Publisher実装やAI生成偽装には使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.3.0"
  agent-directory.status: "active"
  agent-directory.aliases: "Origen,オリジェン,content-origin,content-provenance"
---

# Origen — Production Content Origin / Provenance Trust Gate

## 発動条件

- `Origen` / `origen`を明示されたとき
- Human SourceをAI・外部toolへ渡す前に署名済みRootとして固定するとき
- untrusted outputをPublisherへ渡す直前にPolicy、lineage、build、inspection、exact bytesを検証するとき
- STRICT ORIGINでFinalをSigned Human Source mappingから再構築するとき

途中のAI利用は禁止しない。SNS API、account、schedule、approval UI、投稿本文、Publisher実装、Platform側の判定はこのSkillの責務ではない。

## 所有する境界

```text
Human Source -> secure snapshot -> root-attestor + trusted time -> Signed Human Root
AI / external tools -> Untrusted Output -> trusted builder -> independent Inspector
  -> final-attestor -> atomic publish-bundle -> prepublish receipt -> Project Publisher
```

- Content Planeはasset bytes、Evidence PlaneはOrigen sidecar、Policy、receiptを扱う。Origen固有情報をassetへ埋め込まない。
- PublisherはOrigenへ含めない。Publisherはverified bundle内の`asset`だけをstreamし、再hashし、prepublish後にrewrite/re-encodeしない。
- すべてのCLI operationは`origen-evidence/3`と`origen-trust-policy/1`だけを受理する。旧schemaへのfallbackは持たない。

## 必須不変条件

- CLIはmodeに関係なく`--sign-command` / `--verify-command` / `--adapter-command` / `--inspector-command`を拒否し、Policy内のapproved IDだけを受ける。
- executable、script、config/resourceは実行前にPolicy hashと照合する。署名payloadはPolicy digest、期待identity、toolchain、source map/final snapshot digest、publication representationを含む。
- root-attestorとfinal-attestorを分離する。Production root-attestorは`agent_invocable=false`かつprovider-issued authorization receiptを必須にする。
- `--timestamp`はlocal claimed timeにすぎない。Production Human Rootはapproved timestamp providerのreceipt検証により`trusted_time`を得る。
- pathは`lstat -> O_NOFOLLOW open -> fstat -> size check -> hashしながらprivate copy -> content-addressed snapshot`で固定する。builder/inspectorはsnapshotだけを読む。
- unsupported、malformed、unknown structural property、incomplete inspector coverage、signature/lineage/Policy/hash不一致はfail-closedする。
- STANDARDはAI-generated contentを許すが、集約`content_signals.state=unknown`と`no_unmapped_generated_content=false`を維持する。
- STRICT ORIGINは「Signed Human Source mapping外のgenerated contentをFinalへ導入していない」とだけ保証する。全watermark不存在や生物学的人間が全bytesを作成したことは保証しない。

## 使用するKnowledge

### Required

- [Evidence v3](references/evidence-schema.md)
- [Trust Policy](references/trust-policy.md)
- [Adapters / Final Inspector](references/adapters.md)

### Conditional

- STRICT ORIGINでは[Strict Origin](references/strict-origin.md)を読む。
- external tool接続では[provider protocol](references/provider-protocol.md)を読む。
- Publisher統合では[publisher handoff](references/publisher-handoff.md)を読む。
- format対応判断では[coverage matrix](references/coverage.md)を読む。
- security reviewでは[threat model](references/threat-model.md)を読む。
- C2PA / Content Credentialsでは[standards](references/standards.md)を読む。複雑validationはbuilt-inで再実装しない。

## Production workflow

### Human Root

```bash
python3 skills/origen/scripts/origen.py root HUMAN_SOURCE \
  --policy POLICY.json \
  --creator-id CREATOR_ID --origin-id ORIGIN_ID \
  --signer-id ROOT_SIGNER_ID --verifier-id VERIFIER_ID \
  --timestamp-provider-id TSA_ID \
  --evidence ROOT.origen.json
```

Policyの`creator_key_map`、root-attestor role、key ID、algorithm、signer identity、binary/script/resource hashを実行前に検証する。Root evidenceは`publish_ready=false`である。

### STANDARD finalize

```bash
python3 skills/origen/scripts/origen.py finalize UNTRUSTED \
  --policy POLICY.json --bundle publish-bundle \
  --signer-id FINAL_SIGNER_ID --verifier-id VERIFIER_ID \
  --timestamp-provider-id TSA_ID --root-evidence ROOT.origen.json \
  --source-kind ai-output --guarantee-level standard \
  --transformation 'canonical build' --instruction-actor ai \
  --publication-representation canonical-bytes
```

Built-in Production scopeはTXT、publication profile付きMarkdown、Policy-pinned shape schema付きJSON、PNG。その他はapproved external builderと独立Inspectorが両方揃う場合だけ進む。

### STRICT ORIGIN text

proposal bytesを必須にせず、source mapだけから直接構成できる。

```bash
python3 skills/origen/scripts/origen.py strict-compose \
  --policy POLICY.json --verifier-id VERIFIER_ID --timestamp-provider-id TSA_ID \
  --root-evidence ROOT.origen.json --source-map MAP.json --output COMPOSED.txt
```

続けて`finalize --guarantee-level strict_origin --source-map MAP.json`を実行する。AI proposalを比較入力として使ってもよいが、そのbytesをFinalへcopyしてはいけない。

### Publisher直前

```bash
python3 skills/origen/scripts/origen.py prepublish \
  --policy POLICY.json --bundle publish-bundle \
  --verifier-id VERIFIER_ID --timestamp-provider-id TSA_ID \
  --root-evidence ROOT.origen.json --source-map MAP.json
```

成功stdoutは検証済みreceiptを返す。STRICT textではsource snapshotsから再構築したdigestとbundle `asset` digestを再比較する。

## Interface

- `inspect ASSET --policy POLICY`: secure snapshotに対するread-only検査。公開許可ではない。
- `root`: v3 Human Root evidenceを生成する。
- `strict-compose`: Signed Human text sourcesからFinal候補bytesを直接生成する。
- `finalize`: trusted build、independent inspection、final signature、atomic bundle生成。
- `verify --bundle`: v3 signature、Policy、asset、receipt、lineageを検証。
- `prepublish --bundle`: Production publication boundary。verified receiptを返す。

全commandでPolicyを必須とする。Policyなしの別動作modeや旧CLI fallbackは存在しない。

## Output / error contract

- 成功: stdoutへJSON。
- 拒否: stderrへ`status=rejected`、`publish_ready=false`、machine-readable `error.code`、非0終了。
- inputをin-place変更しない。既存output/bundleを上書きしない。partial bundleをpublish-readyとして残さない。

## 配布元での検証とimport

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests
```

利用側へは既存copyを手編集せず、配布元のexact commitからimportする。

```bash
bash tools/import-skill.sh origen --target /path/to/consumer-root
```

`agents/upstream.yaml`のsource repository、commit、versionを保持する。自動同期、Project state複製、Publisher同梱はしない。

## 禁止事項

- secrets、private key、credential、実provider responseをrepositoryへ置かない。
- adapter自己申告だけでpublish-readyにしない。
- `not_detected`や`unknown`をcleanと呼ばない。
- 有効なC2PAを無検証で削除しない。Policyによりvalidate後のpreserve / reissue / explicit detachを選ぶ。
- AI由来をHuman由来として表現しない。
- 未対応形式をbuilt-in対応と記述しない。
