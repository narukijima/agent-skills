# Threat model

## Protected assets

- Human Root bytes、authorization boundary、root-attestor identity、trusted time
- Final snapshot bytes、source mapping、toolchain/Policy claims
- Publication BoundaryでPublisherへ渡すexact bundle bytes

## In-scope attackers / failures

- symlink、rename/path swap、hardlink/background mutation、TOCTOU
- arbitrary signer/verifier/builder/inspector、binary/script/resource replacement
- backdated local timestamp、key/role/provider confusion、authorization receipt substitution
- duplicate/nonfinite/deep JSON、unknown critical field、invalid cross-field state
- unsigned operation resource、URL/base64/network fetch、adapter self-claim
- C2PA/metadata/active content見落とし、resource/process/archive/decompression bomb
- output race、overwrite、partial bundle、post-finalize asset/evidence mismatch

## Controls

secure snapshot、config alias resolution、Provider registry hash check、authorization receipt、role separation、trusted time receipt、strict parser、typed operations、independent coverage、atomic no-replace bundle、prepublish revalidation。

## External assumptions / residual limits

- Root signatureはidentity assertionでありhuman authorship forensic proofではない。
- OS-level network deny、read-only mount、key custody、HSM policy、dual approvalはProject/Providerが実装する。Origenはportable protocolとEvidence bindingを持つ。
- 無人運用で`interaction: none`を選ぶことは、同一ユーザ権限のprocessから秘密鍵を使える状態を受け入れる判断である。Origenはこれを隠さず宣言・実測させ、漏洩疑いをrotationで扱う。AIが任意contentをRoot keyで署名する経路はauthorization receipt要求で塞いだままにする。
- approved external Inspectorのparser/decoder correctnessとtrust listは外部tool責務。
- grapheme boundaryはPython Unicode databaseに基づくconservative implementationで、versionをEvidenceへ記録する。
- atomic no-replace directory publicationはruntime adapterが提供する。現在のprocess runtimeは対応するPOSIX primitiveを選択し、利用不能ならfail closedする。
- Platform-side rewrite/re-encode/classificationは保証しない。
