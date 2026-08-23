# Standards boundary

## C2PA 2.4

Origen built-inはmarker/carrier discoveryを行うが、manifest validation、certificate/trust list/revocation、reissueを独自実装しない。approved C2PA SDK/c2patool相当をexternal Inspector/Builderとしてpinする。

検出対象: PNG caBX、JPEG JUMBF/APP11、RIFF C2PA、ID3 GEOB C2PA、ISO BMFF C2PA/JUMBF、HTML inline/external manifest、SVG metadata、Markdown structured block、U+FEFF/Variation Selector/C2PATXT、PDF embedded manifest、external `.c2pa`。

valid credentialはvalidateしoriginal manifest digest/statusをEvidenceへ記録後、Policyでpreserve/reissue/explicit detachを選ぶ。黙って削除しない。

正本: [C2PA 2.4](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)、[CAI SDK / c2patool](https://opensource.contentauthenticity.org/)。

## Base representations

SHA-256、RFC 3339 UTC、IANA media type、deterministic JSONを使う。Origen JSONはRFC 8785準拠を標榜しない。signature algorithm、PKI、TSA、trust listはPolicy-pinned external providerが所有する。
