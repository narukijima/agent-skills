# Threat model

## Protected assets

- Human Root bytesとroot-attestor identity/trusted time
- Final snapshot bytes、source mapping、toolchain/Policy claims
- Publication BoundaryでPublisherへ渡すexact bundle bytes

## In-scope attackers / failures

- symlink、rename/path swap、hardlink/background mutation、TOCTOU
- arbitrary signer/verifier/builder/inspector、binary/script/resource replacement
- backdated local timestamp、key/role/creator mapping confusion
- duplicate/nonfinite/deep JSON、unknown critical field、invalid cross-field state
- unsigned operation resource、URL/base64/network fetch、adapter self-claim
- C2PA/metadata/active content見落とし、resource/process/archive/decompression bomb
- output race、overwrite、partial bundle、post-finalize asset/evidence mismatch

## Controls

secure snapshot、Policy ID resolution/pre-execution hash、role separation、trusted time receipt、strict parser、typed operations、independent coverage、atomic no-replace bundle、prepublish revalidation。

## External assumptions / residual limits

- Root signatureはidentity assertionでありhuman authorship forensic proofではない。
- OS-level network deny、read-only mount、HSM policy、dual approvalはProject/providerが実装する。Origenはcontractとevidence bindingを持つ。
- approved external Inspectorのparser/decoder correctnessとtrust listは外部tool責務。
- grapheme boundaryはPython Unicode databaseに基づくconservative implementationで、versionをEvidenceへ記録する。
- atomic no-replace directory renameはmacOS `renamex_np(RENAME_EXCL)` / Linux `renameat2(RENAME_NOREPLACE)`があるplatformに限定する。
- Platform-side rewrite/re-encode/classificationは保証しない。
