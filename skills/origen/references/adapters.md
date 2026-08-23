# Builders and Final Inspectors

BuilderとInspectorは別authorityである。adapterの`output-validated`や`provenance-inspected`自己申告だけでpublish-readyにしない。

## Built-in Production scope

- TXT: strict UTF-8、BOM/NFC/LF policy、hidden/control拒否、canonical build。
- Markdown: TXT処理に加えPolicy publication profileでraw HTML/front matter/commentを判定し、structured C2PA blockを検出。
- JSON: duplicate/NaN/Infinity拒否、hash-pinned `origen-json-shape/1`検証、canonical sorted build。
- PNG: chunk/CRC/IHDR/IDAT/scanline、dimension/pixel/decoded limits、identity container rebuild。caBX/C2PA/ICC/APNGはbuilt-in detachしない。

## External builder

Policy-pinned absolute executableだけを起動し、input snapshotとoutput directoryだけを渡す。network fetch、URL/base64/freeform binary/shell argumentはtyped operationに持ち込めない。builder終了後のoutputを再snapshotし、元output pathはFinal判定に使わない。

## Independent Final Inspector

external formatは別approved Inspectorが次を全て`unknown`なしで返す。

- file type、container validity、MIME/extension consistency
- metadata、C2PA、EXIF/XMP/IPTC
- active content、embedded files、external references
- decodability、resource limits、Policy coverage

STRICT external mediaはoperation schema、source binding、outputも独立再検証する。builderと同一approved identity/scriptは拒否する。

Provider detectorが`detected`ならPolicy gateは拒否する。`not_detected`でもFinal aggregate content signalは`unknown`のまま。

## C2PA

C2PA markerを検出したinputはapproved C2PA SDK/c2patool相当Inspectorでoriginal provenanceをvalidateし、manifest digest/statusをEvidenceへ記録してからPolicyのpreserve/reissue/detachを実行する。複雑validationをOrigen built-inで再実装しない。
