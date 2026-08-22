# File type adapters

Origen 0.1は共通CLIの内部でMIME/containerを検出し、形式別adapterへrouteする。拡張子だけでは判定しない。

## 内蔵Clean Build

| family | 対応 | 処理 |
| --- | --- | --- |
| plain text / Markdown | built-in | strict UTF-8 decode、Unicode NFC、LF改行、末尾改行1つで新規bytesを生成 |
| JSON | built-in | duplicate key / NaNをrejectし、UTF-8・sorted key・compact JSONとして再生成 |
| PNG | built-in | signature/chunk/CRC/critical chunk/IDAT zlib/scanlineを検証し、許可した表示用chunkと再圧縮IDATから新containerを生成 |

PNGはnon-interlaced、標準color type / bit depthだけを受け入れる。text、XMP、EXIF、time、C2PA/JUMBF等のancillary chunkを継承しない。ICC profileや未知critical chunkなど、安全に意味を保持できない構造はrejectする。

## 外部trusted adapterが必要

| family | 期待するtoolchain例 | Origen 0.1の既定動作 |
| --- | --- | --- |
| HTML | parser / sanitizer / serializer + active-content policy | reject |
| YAML | schema-aware safe parser + canonical emitter | reject |
| JPEG / WebP / other image | full decode + trusted encoder + metadata/provenance inspection | reject |
| audio | decoder + trusted re-encoder + stream/metadata validation | reject |
| video | decoder/remux/transcode + stream/container validation | reject |
| PDF | parser + supported trusted rebuild + render/structure validation | reject |
| unknown binary | format-specific inspector and rebuild guaranteeがない | reject |

`ffmpeg`、ImageMagick、Pillow、qpdf、Ghostscript等がPATHにあるだけでは信頼済みにならない。Projectがversion、option、format policy、output validationを固定したadapter commandを明示する。

## Inspectionとfinalizationの違い

`inspect` はknown metadata/provenance、MIME mismatch、container異常を報告するread-only操作であり、公開許可ではない。`provenance_status = clean` も「検出できる埋め込みがない」ことだけを示す。

`finalize` は必ず新しいoutputを作り、input fileをin-place変更しない。外部adapterを使う場合も、Origenがoutputを再検査・hashしてsigned evidenceへ固定する。

## Fail-closed

次は `publish_ready = false` としてrejectする。

- magic bytesと宣言/拡張子が矛盾する
- invalid UTF-8、duplicate JSON key、NaN/Infinity
- truncated/corrupt PNG、CRC error、interlaced PNG、unknown critical chunk、unsupported color profile
- known provenance containerがfinal outputに残る
- external adapterが必須guaranteeを返さない
- input/outputのfamilyが意図せず変わる
- format、provenance状態、再構築結果のいずれかがunknown
