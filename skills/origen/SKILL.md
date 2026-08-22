---
name: origen
description: Human Rootを署名固定し、Structural/Content provenanceを分離してSTANDARDまたはSTRICT ORIGINでpublish-readyを判定する。投稿やAI生成偽装には使わない。
license: MIT. See LICENSE.txt
metadata:
  agent-directory.version: "0.2.0"
  agent-directory.status: "active"
  agent-directory.aliases: "Origen,オリジェン,content-origin,content-provenance"
---

# Origen — Content Origin / Provenance境界

## 発動条件

- 利用者が `Origen` / `origen` を明示したとき
- Human Sourceのhash・identity・署名・timestampをAI処理前に固定するとき
- AI・外部service由来のassetをPublisherへ渡す前に検査、再構築、検証するとき
- 最終assetから署名済みHuman Rootまたは親assetまでの派生関係を検証するとき

AIの使用自体、特定SNSへの配信、AI生成物を人間生成物に見せかける処理、単なるmetadata/AI label除去には使わない。

## 目的

Content PlaneとEvidence Planeを分け、次の境界を所有する。

```text
Human Source -> signed Human Root -> AI / tool processing
External / AI output -> untrusted -> Origen -> publish-ready asset -> Project Publisher
```

- **Root**: content hash、asset identity、creator/origin identity、外部署名、timestampをsidecar evidenceへ固定する。
- **Final / Pre-Publish**: untrusted inputを形式別に検査し、trusted toolchainで再構築してから最終hash・派生証拠・`publish_ready`を確定する。

Origenが保証するのは技術的に確立できるprovenance propertyだけである。Structural Cleanはcontainer/metadataの検査結果であり、pixel、waveform、frame、token等のContent-Level信号が存在しないことを意味しない。

このSkillはprovenance domainの入力、変換、安全ゲートだけを扱う。shell、filesystem、network、sandbox、provider execution mode等のGeneric Runtime Permissionを設定・判定しない。Publisher、投稿先、account、公開時刻、文体、KPIは利用側Projectが所有する。

## 不変条件

- AI・外部serviceのoutputを直接Publisherへ渡さない。Publisherは `prepublish` が成功したassetだけを受け取る。
- Structural Provenanceの `unknown`、unsupported、署名未検証、hash不一致、lineage不一致は常に `publish_ready = false` として止める。
- Content-Level Provenanceの `unknown` はSTANDARDだけで許容し、結果へ明記する。STRICT ORIGINではdenyする。
- `Structural Clean ≠ Content-Level Clean`、`Unknown ≠ Clean`、`Re-encoded ≠ Content signal absent` を維持する。
- metadata削除だけで成功にしない。内容・containerを検査し、内蔵または明示されたtrusted adapterでClean Buildする。
- `source_kind` と変換内容を証拠へ残す。AI由来をHuman由来と偽装しない。
- 秘密鍵をrepository、manifest、command line、Agent-readable fileへ保存しない。署名・検証はKMS、hardware-backed key、外部署名service等へ接続するprovider commandへ委ねる。
- Origen sidecarをC2PA Content Credentialと呼ばない。C2PA/CAWGを使う場合は適合toolchainを外部adapter/providerとして接続する。

## 使用するKnowledge

### Required

- `references/evidence-schema.md` — evidence、署名、lineage、publish-readyの契約
- `references/adapters.md` — file type別の内蔵対応とfail-closed条件
- `references/strict-origin.md` — STRICT ORIGIN source mappingとfile type別の追跡契約

### Conditional

- 条件: KMS、HSM、external signing serviceまたはtrusted rebuild toolを接続する
  参照: `references/provider-protocol.md`
- 条件: C2PA / Content Credentialsとの併用、または設計境界を確認する
  参照: `references/standards.md`

## 高レベル手順

### Human Rootを確定する

1. 一次asset、安定した `creator-id` / `origin-id`、外部署名providerを確認する。
2. 次を実行し、assetとは別のEvidence Planeへroot evidenceを保存する。

```bash
python3 skills/origen/scripts/origen.py root SOURCE \
  --creator-id CREATOR_ID --origin-id ORIGIN_ID \
  --sign-command '/trusted/origen-sign-provider' \
  --evidence ROOT.origen.json
```

3. `verify` でasset hashと署名を確認する。root evidence自体は公開許可ではない。

### Assetを公開可能にする

1. AI・外部tool outputを常にuntrusted inputとして隔離する。
2. `inspect` でMIME、container、known provenance/metadataを確認する。
3. `finalize` を実行する。内蔵adapterで未対応なら、保証契約を満たすtrusted adapterを指定する。

```bash
python3 skills/origen/scripts/origen.py finalize UNTRUSTED \
  --output FINAL --evidence FINAL.origen.json \
  --guarantee-level standard \
  --source-kind ai-output --transformation 'edited from signed root' \
  --root-evidence ROOT.origen.json \
  --sign-command '/trusted/origen-sign-provider' \
  --verify-command '/trusted/origen-verify-provider'
```

### STRICT ORIGINで確定する

1. primary Human Rootと、必要なら別途署名したHuman追加sourceを用意する。
2. `references/strict-origin.md` のsource mapで、text spanまたはmedia primary sourceと決定的変換を宣言する。
3. AI生成contentではなくHuman sourceから再構成できることを`finalize`に検証させる。

```bash
python3 skills/origen/scripts/origen.py finalize PROPOSED_FINAL \
  --output FINAL --evidence FINAL.origen.json \
  --guarantee-level strict_origin --source-map SOURCE_MAP.json \
  --source-kind human-edit --transformation 'select and reorder Human spans' \
  --root-evidence ROOT.origen.json \
  --sign-command '/trusted/origen-sign-provider' \
  --verify-command '/trusted/origen-verify-provider'
```

STRICT `prepublish` でも同じsource mapとroot evidenceを再提示し、署名済みsummaryとsource bytesを再検証する。

### Publisher直前に再検証する

どちらのmodeもPublisherへ渡す直前に`prepublish`を再実行する。STRICTでは`--source-map`も指定する。

```bash
python3 skills/origen/scripts/origen.py prepublish FINAL \
  --evidence FINAL.origen.json \
  --root-evidence ROOT.origen.json \
  --verify-command '/trusted/origen-verify-provider'
```

## Interface

- `inspect ASSET`: read-only inspection。成功しても公開許可ではない。
- `root ASSET`: Human Root evidenceを作成する。外部署名必須。
- `finalize ASSET`: `--guarantee-level standard|strict_origin` に従い、Clean Build後の新規assetとsigned final evidenceを作成する。
- `verify ASSET`: hash、署名、必要なroot/parent linkを検証する。
- `prepublish ASSET`: `verify` に加え、final evidenceと `publish_ready = true` を必須にするPublisher直前ゲート。

詳細optionは `python3 skills/origen/scripts/origen.py <command> --help` で確認する。決定処理をAgentの即席コードで置き換えない。

## 配布元での検証とimport

変更後は公開repository rootで次を実行する。

```bash
bash tools/validate-skills.sh
python3 -m unittest discover -s tests
```

利用側Projectへは既存copyを手編集・上書きせず、配布元の記録済みcommitから明示的にimportする。

```bash
bash tools/import-skill.sh origen --target /path/to/consumer-root
```

importerが作る `skills/origen/agents/upstream.yaml` のsource repository、commit、versionを保持する。Runtime固有adapterが必要なら、利用側rootがcanonical `skills/origen/SKILL.md` を参照する薄いadapterとして所有する。自動同期、Project stateの複製、Publisher実装の同梱はしない。

## 出力契約

- 成功: stdoutへJSON。`publish_ready`、`guarantee_level`、`structural_provenance`、`content_provenance`、`root_verified`を返す。
- 不明: stderrへ `status: rejected` とmachine-readable error code、非0終了。assetをPublisherへ渡さない。
- 失敗: inputを変更せず非0終了。途中生成物をfinal outputとして残さない。

## 禁止事項

- AI/external outputをOrigenの外からPublisherへ渡さない。
- `inspect` 成功、metadata消失、file open成功だけを `publish_ready` と解釈しない。
- unsupported形式、unknown binary、壊れたcontainer、未検証signature/lineageを推測で通さない。
- AI由来、外部tool由来、変換eventをHuman-only provenanceとして表現しない。
- STANDARDの`content_provenance = unknown`を`verified_clean`へ読み替えない。
- STRICT ORIGINでsource map外のliteral、AI-generated wording/pixel/waveform/frame、未署名sourceを混ぜない。
- 秘密鍵、credential、実provider responseを保存・出力・コミットしない。
- OrigenにSNS API、Project固有のapproval、schedule、公開操作を追加しない。
