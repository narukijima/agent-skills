# Standards boundary

## Agent Skills

OrigenはAgent Skillsの標準layoutに従う。

- `SKILL.md`: 発動契約と最小workflow
- `references/`: evidence、adapter、provider、標準境界
- `scripts/`: 再利用する決定的処理

正本仕様: <https://agentskills.io/specification>

## C2PA / Content Credentials

C2PA 2.4はasset provenanceをcryptographic binding、signed claim、ingredient/action、validation stateで表す既存標準である。Origenはこれを再実装せず、次の境界で共存する。

- embedded/external C2PA manifestをinspection対象として扱う
- C2PA/CAWG生成・検証は適合SDK/toolを外部provider/adapterとして接続する
- Origen sidecarをC2PA manifestやContent Credentialと呼ばない
- C2PAを除去してAI由来を隠す用途にしない。Origen evidenceの `source_kind` とtransformationを保持する
- 有効なC2PAを継承・更新すべきpolicyでは、削除して独自sidecarだけに置き換えず、C2PA-aware adapterでingredient/action chainを更新する

正本仕様:

- C2PA 2.4: <https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html>
- Human / organizational identity recommendation: <https://spec.c2pa.org/specifications/specifications/2.4/identity/identity.html>
- CAI SDK / c2patool: <https://opensource.contentauthenticity.org/>

## 基礎表現

- Content digest: SHA-256
- Time: RFC 3339 UTC
- Media type: IANA media type
- Signature algorithm、certificate、trust list、revocation、timestamp authority: external provider policy

Origen JSONは限定型のdeterministic serializationを使うが、RFC 8785準拠を標榜しない。将来、相互運用が必要になった時点で標準canonicalizationまたはC2PAへ移行し、独自仕様を拡張し続けない。

## Guarantee boundary

Origenは「すべてのAI watermarkを除去する」「AI signalが存在しない」とは主張しない。STANDARDは検証可能なStructural Provenanceだけをclean判定し、Content-Levelを`unknown`として保持する。STRICT ORIGINはC2PAの代替規格ではなく、署名済みHuman source mapping外のcontentをFinalへ入れないlocal publication policyである。
