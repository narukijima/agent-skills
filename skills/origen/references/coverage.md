# Phase 1 coverage matrix

`Built-in`はProduction finalizeでexternal builderなしに成立する範囲。`Inspect`だけのmarker/resource checkはBuilt-in finalize対応を意味しない。

| Format | STANDARD | STRICT ORIGIN | Structural coverage | Content signal guarantee | Built-in | External Adapter | Current Gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TXT | canonical UTF-8 build | source-map compose | controls/C2PA text markers | unknown | Yes | Optional | advanced linguistic boundaries |
| Markdown | profile + canonical build | source-map compose | raw HTML/front matter/comments/C2PA block | unknown | Yes | Optional | renderer-specific semantics beyond profile |
| JSON | pinned shape + canonical build | Not built-in | strict JSON + shape | unknown | Yes | Full JSON Schema optional | arbitrary JSON Schema dialect |
| YAML | External only | External only | unknown built-in | unknown | No | Required | mature parser/serializer not bundled |
| HTML | External only | External only | marker/active-content discovery only | unknown | No | Required | safe parser/sanitizer/renderer |
| SVG | External only | External only | marker/active-content discovery only | unknown | No | Required | safe XML/SVG parser and resource closure |
| JPEG | External only | typed op + Inspector | marker discovery; otherwise unknown | unknown | No | Required | decoder/encoder/C2PA validator |
| PNG | identity clean build | signed identity clean build | chunk/CRC/IDAT/dimensions/metadata/C2PA | unknown | Yes | C2PA/color/APNG | non-identity pixels require external |
| WebP | External only | External only | RIFF marker discovery only | unknown | No | Required | decoder/encoder/animation |
| GIF | Unsupported | Unsupported | unknown | unknown | No | Not defined | approved adapter/Inspector contract needed |
| WAV | External only | typed op + Inspector | RIFF/C2PA discovery only | unknown | No | Required | decoder/sample validation |
| MP3 | External only | typed op + Inspector | ID3/GEOB discovery only | unknown | No | Required | decoder/tag/sample validation |
| AAC / M4A | AAC unsupported; M4A external | M4A typed op + Inspector | M4A ISO BMFF marker discovery only | unknown | No | M4A required | raw AAC detection/decoder contract |
| FLAC | Unsupported | Unsupported | unknown | unknown | No | Not defined | approved adapter/Inspector needed |
| MP4 | External only | typed op + Inspector | ISO BMFF marker discovery only | unknown | No | Required | frame/container/C2PA validation |
| MOV | External only | typed op + Inspector | ISO BMFF marker discovery only | unknown | No | Required | frame/container/C2PA validation |
| WebM | Unsupported | Unsupported | unknown | unknown | No | Not defined | EBML parser/adapter needed |
| PDF | External only | External only | header/EOF and embedded C2PA discovery only | unknown | No | Required | active content/embedded file/render validation |
| ZIP | Unsupported finalize | Unsupported | bounded entry/ratio inspection only | unknown | No | Not defined | archive content publication policy |
| generic binary | Unsupported | Unsupported | unknown | unknown | No | Not defined | explicit format contract required |

STANDARDはAI-generated contentを許すがContent signal absenceやHuman originを保証しない。STRICT ORIGINはSigned Human Source mapping外のgenerated contentをFinalへ導入していないことを保証するだけで、source内部の未知signal不存在を保証しない。
