# File type adapters

Origenはmagic/containerとMIMEから形式別adapterへrouteし、inputをin-place変更せず新規final assetを作る。

## 内蔵Clean Build

| family | STANDARD | STRICT ORIGIN |
| --- | --- | --- |
| plain text / Markdown | strict UTF-8、BOM除去、NFC、LF、不可視/control文字拒否 | signed Human sourceのsliceと固定whitespace separatorだけで再構成 |
| JSON | duplicate key / NaN拒否、文字policy、sorted compact JSON再生成 | v0.2内蔵source mappingは未対応。将来schema-aware mappingが必要 |
| PNG | chunk/CRC/IDAT zlib/scanline検証、新container生成 | signed Human PNGのidentity/container rebuild。生成pixelの入力は拒否 |

Textのleading BOMは除去する。embedded BOM、Unicode category `Cf`、TAB/LF/CR以外のcontrol characterはrejectする。これはhidden/invisible structureへのpolicyであり、統計的・token-selection watermarkの除去保証ではない。

## HTML / SVG

内蔵inspectionはscript、event handler、iframe/object/embed/foreignObject、JavaScript URL、refresh等を検出する。安全なparser/sanitizer/serializerを内蔵していないため、finalizeにはtrusted external adapterが必要である。検出patternがないことだけで安全とは判定せず、adapterの`provenance-inspected`保証とfinal再検査を両方要求する。

## Media / documents

JPEG、WebP、audio、video、PDF、その他documentは次を実行するtrusted adapterが必要である。

```text
decode / parse
→ trusted deterministic processing
→ trusted encoder / generator
→ new container
→ output validation
→ Origen final inspection
```

metadata stripやremux成功だけをClean Buildとしない。STANDARDではpixel/waveform/frame/token levelの信号を検証できないため`content_provenance=unknown`を返す。

STRICTではadapter inputをsource mapで検証したsigned Human assetだけに限定し、追加で次の保証を要求する。

- `human-origin-inputs-only`
- `deterministic-transformation`
- `content-origin-mapped`

AI-generated pixel、waveform、frame、PDF bytesをHuman sourceとして渡した場合はhash/source mapping不一致で拒否する。

## Structural inspection

Structural statusは次の意味を持つ。

- `clean`: prohibited metadata/manifest/active fieldがないことをadapter範囲でfinal再確認した
- `detected`: prohibited structural propertyを検出した
- `unknown`: format/inspectorでは保証できない

外部adapterの再構築後もknown marker、metadata、active contentが残れば拒否する。C2PA等をfinal用に正しく再発行した場合だけ`embedded_provenance=validated-final`を許可し、AI provider由来manifestの黙示継承には使わない。

## Fail-closed

- STANDARD: Structural `detected/unknown`、malformed/unsupported、rebuild/verification failure、broken signature/lineageを拒否。Content `unknown`だけは許容する。
- STRICT ORIGIN: 上記に加え、root/source map不足、未知span/frame/sample、AI/external generated content、Human source hash不一致、決定性保証不足を拒否する。
