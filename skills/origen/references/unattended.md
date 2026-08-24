# Unattended Signer Profile v1

無人運用（background job / scheduler / 非対話Runtime）でOrigenを回すには、Providerが「人間の操作を待たない」ことが必要である。Origenの思想はもともと無人運用側に立っている。Human Rootは毎回の手動クリックではなく、署名対象digestへ結び付いたauthorization boundary receiptで保証する。承認はcontent単位ではなくworkflow単位である。

足りなかったのは、**どのProviderが無人経路として適格かを仕様が言えること**だった。このprofileがそれを定める。

## 宣言 → 実測 → 強制

| 段 | 場所 | 何をするか |
| --- | --- | --- |
| 宣言 | Provider registry entryの`interaction` | Providerが無人適格を名乗る |
| 実測 | `origen setup` / `origen doctor` | 名乗りが本当かをbounded deadlineで測る |
| 強制 | configの`unattended` | 実運用commandが未宣言Providerを拒否する |

宣言だけを信用しない。これが核心である。

## interaction

Provider registryの`providers.<id>`（またはalias entry）へ次のいずれかを書く。

- `none`: 署名にいかなる人間の操作も要らない。**無人適格はこれだけ。**
- `per-launch`: Provider processまたは元アプリの起動ごとに承認が要る。
- `per-signature`: 署名ごとに承認が要る。

未宣言（fieldなし）は「不明」であり、無人適格ではない。`unattended: true`のconfigは未宣言Providerを`UNATTENDED_PROVIDER_REQUIRED`で拒否する。値が3値以外なら`INVALID_INTERACTION_DECLARATION`で拒否する。

`interaction`はdeployment情報であり、Trust Policy digestへ含めない。宣言を追加してもEvidenceの互換性は変わらない。

## 実測conformance check

`interaction: none`を宣言したProviderに対してだけ、`setup`と`doctor`は次を実行する。

1. `health` / `capabilities` / `get_public_key`
2. **秘密鍵を実際に使う** `sign` と、その結果の `verify`（timestamp Providerは`timestamp` / `verify_timestamp`）
3. すべてを `resource_limits.unattended_probe_timeout_seconds`（既定10秒）以内に完了させる

超過は`UNATTENDED_PROBE_TIMEOUT`である。probeはsanitized environmentと制御TTYなしのpipeで走るので、schedulerが与える条件をそのまま再現する。

秘密鍵を必ず使うのは、**鍵の一覧が即答でも署名だけが止まるProviderが実在する**からである。`health`と`get_public_key`だけを測ると、そういうProviderは全項目を通過してしまう。

### Root keyのprobe

Root keyは`interaction: none`を宣言したときだけ署名probeの対象になる。probe payloadは次のdomain-separated documentであり、`schema_version`が`origen-evidence/4`ではないのでEvidence proofとして再利用できない。

```json
{
  "schema_version": "origen-signer-self-test/1",
  "algorithm": "Ed25519",
  "key_id": "...",
  "nonce": "RANDOM_SHA256",
  "role": "root-attestor"
}
```

無人適格を名乗らないdeploymentでは、Root keyはself-testで一切使わない（`self_test: skipped`）。

## 既知の不適格経路

実測（2026-08-25 / macOS Darwin 25.5 / arm64）で確認した経路。

| 経路 | 鍵の一覧 | 署名 | 実際の`interaction` |
| --- | --- | --- | --- |
| password manager SSH agent | 即答 | アプリ単位の承認ダイアログ待ちで無応答 | `per-launch` |
| OS login key store | — | key store ACLの承認ダイアログ待ちで無応答 | `per-launch` |

どちらも承認を元アプリの終了までしか保持しない。password managerやOSの再起動ごとに、次の署名で全自動処理が停止する。これは設定ミスではなく両者の設計である。仕様上はどちらも正当なProviderに見えるので、踏むまで分からない。だからconformance checkを置く。

## 適格な経路

- repository外の`0600` Ed25519鍵ファイル + SSHSIG（agentもdaemonも介さない最小構成）
- cloud KMS / HSM / PKCS#11 / remote signer（credentialがprocessへ届く限り`interaction: none`を名乗れる典型例）

いずれもOrigen coreへ実装しない。Origenは`key_id`、`algorithm`、canonical payloadだけを渡し、秘密鍵を受け取らない。

## 受け入れるrisk

`interaction: none`は「そのユーザ権限のprocessから秘密鍵を使える」ことを意味する。これは無人化の対価として**明示的に受け入れる設計判断**である。隠さない。

- 鍵はrepository外に置き、`0600`、symlinkでないregular fileにする。
- 漏洩が疑われたらrotationで対応する（[Trust Policy / config](trust-policy.md)のrotation手順）。
- Root keyとFinal keyを分け、Root側のauthorization boundaryを別鍵・別承認にする。

避けるべきなのは「AIが自由にRoot signerを呼び、任意の文章をHuman Rootとして署名できる」経路だけである。これはProviderのauthorization receipt要求で塞がっている。無人化はこの要求を緩めない。

## 人間が残る場所

| 処理 | 人間 |
| --- | --- |
| 初回Signer登録 | 必要 |
| Workflow承認（boundary登録） | 必要 |
| 鍵生成 / rotation / recovery | 必要 |
| 各ContentのRoot署名 | 不要 |
| AI編集 | 不要 |
| Final署名 | 不要 |
| verify / prepublish / publish | 不要 |

## verify専用に残したProvider

rotation後もEvidence検証のために旧alias、旧key ID、旧verifier recordをregistryへ残す。無人運用ではその旧entryも`interaction: none`でなければならない。`verify`と`prepublish`は旧signerのProviderへ`verify` / `verify_authorization`を投げるので、そこで承認ダイアログが出れば無人検証は止まる。`unattended: true`のconfigはEvidenceから解決した旧signerと旧timestamp Providerにも宣言を要求する。

## reference実装

`providers/unattended-file-signer.py`はOrigen coreではなくdeployment側の例である。repository外の`0600` Ed25519鍵ファイルとOpenSSH SSHSIGだけを使い、agent socketもkey storeも介さない。

```bash
python3 skills/origen/providers/unattended-file-signer.py init \
  --home /secure/path/origen-keys
```

- `root` / `final` / `authorization` の3鍵を生成し、Provider registry fragmentをstdoutへ出す。既存鍵があれば上書きせず停止する。
- `authorization`鍵がworkflow boundaryを表す。`authorize_root`は`provider_authorization` boundaryのreceiptをこの鍵で発行し、`verify_authorization`で再検証する。receiptは`subject_sha256`へ束縛されるので、別contentへ流用できない。
- 鍵置き場は`ORIGEN_FILE_SIGNER_HOME`で渡す。registry entryの`inherit_environment`へ変数名だけを書き、値をregistryへ保存しない。
- `expected_executable_sha256`と`expected_script_sha256`は他のProviderと同様にdeployment側でhash-pinする。
- signer専用である。trusted timeは引き続きexternal timestamp Providerから得る。

## 無人deploymentの手順

```bash
python3 skills/origen/scripts/origen.py setup \
  --provider-registry /path/to/providers.json --unattended

python3 skills/origen/scripts/origen.py doctor
```

`setup --unattended`はconfigへ`unattended: true`を書き、3 Providerすべてに`interaction: none`を要求し、実測probeを通す。`doctor`は同じ検査をconfig既存のdeploymentへ何も書かずに実行する。scheduler投入前とcredential環境が変わった直後に`doctor`を通す。
