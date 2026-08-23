# STRICT ORIGIN

STRICT ORIGINはdetector推測ではなく、Final bytesをSigned Human Sourceとtyped operationsへ決定的に結び付ける。正しいclaimは「Signed Human Source mapping外のgenerated contentをFinalへ導入していない」である。

## Source map v2

```json
{
  "schema_version": "origen-source-map/2",
  "kind": "text",
  "instruction_actor": "ai",
  "sources": [{"source_id": "root", "asset": "human.txt", "evidence": "root.json"}],
  "operations": [
    {"op": "slice", "source_id": "root", "start": 0, "end": 10, "boundary": "word"},
    {"op": "separator", "value": "\n"}
  ]
}
```

全source asset/evidenceをsecure snapshotし、v4 Human Root signature、authorization boundary、Policy digest、trusted timestamp、hash/media/sizeを確認する。primary Rootを含むmulti-rootを許す。Human additionは新しいsigned sourceが必須。

## Strict text

`strict-compose`はsource mapだけからTXT/Markdown bytesを生成する。AI proposalはoptional comparison inputであり、そのbytesはFinalへcopyしない。summaryはsource map digest、mapping digest、rebuilt output digest、instruction actorを含む。

default slice境界はgrapheme/token/word/line/paragraph。grapheme clusterを分割せず、複数の1-grapheme sliceによるletter-by-letter synthesisをdefault拒否する。任意code-point sliceはadvanced Policyだけ。separatorは既存のwhitespace setだけ。

prepublishはsource snapshotsから再composeし、rebuilt digest、signed summary、Final snapshot digestを比較する。

## Typed media operations

`origen-operation/1`のPhase 1 allowlist:

`identity`, `crop`, `resize`, `rotate`, `trim`, `concat`, `resample`, `gain`, `channel-map`, `mux`, `overlay-signed-asset`, `render-signed-text`, `add-signed-subtitle`。

URL、base64、freeform binary、unsigned text/image/logo/subtitle、AI mask、shell/network parameterは禁止。content-bearing resourceはsource IDでSigned Human Sourceへ結ぶ。非identity mediaはapproved external builderと独立Inspectorが必要。

`instruction_actor=ai|human|tool|mixed`、`content_basis=signed_human_sources`、`builder_actor=trusted_builder`を別々にEvidenceへ記録する。
