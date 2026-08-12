# Posting safety contract

通常テキスト投稿は不可逆な外部作用である。UI上の確認や自然言語の指示ではなく、manifest、identity readback、SQLite transaction、budget、reconcileで強制する。

## Required gates

1. Projectがnormalized後の本文、`content_id`、stable `expected_user_id`、`app_id`、OAuth app credential fingerprintを確定している。
2. approval authorityが同じ本文hash、account、app fingerprintへ一意な`approval_id`を発行している。
3. approval gatewayの`prepare`が`valid: true`の短期manifestを0600で保存し、隔離された`X_API_MANIFEST_SIGNING_KEY`でHMAC-SHA256署名している。
4. `X_POSTING_ENABLED=true`、exact `X_API_WRITE_MAX_CALLS=3`、Project / Agent別daily limitがgateway-owned設定として明示されている。
5. gateway-owned `X_API_APP_ID`がmanifestの`app_id`と一致し、実credentialのpublic app IDから導出したfingerprintがmanifestと一致し、`/2/users/me`が`expected_user_id`とexact matchする。
6. canonical SQLite ledgerにsent / unknown duplicateがなく、attempt budgetが残る。
7. resultをpost IDまたは`reconcile`で照合できる。

どれか不成立ならlive sendを行わない。manifestの`approval_id`とHMACはapproval systemの代替ではなく、Project側の承認証拠を固定payloadへbindingする。署名鍵、予算値、credential、script、SQLiteを同じ一般Agentへ渡す構成では、Agent自身による改変を防げない。

## Manifest contract

`prepare`は本文をNFCへ正規化してからvalidation、SHA-256、weighted lengthを計算する。manifestにはschema version、content / app / credential fingerprint / expected account / approval ID、created / expiry、normalized text / hash、weighted length、OAuth refreshを含む最大3-call planを保存する。全体hashに加えgateway-owned secretによるHMAC-SHA256を付け、署名鍵を持たないcallerによる再計算改ざんを拒否する。

`send`はmanifest外の本文、file、account override、ledger pathを受け取らない。invalid / expired / tampered manifestはcredential解決、identity API、ledger attempt、external writeより前に拒否する。

validationは最低限、空白のみ、control character、NFC、weighted limit 280、URL count、cashtag count、quote target countを扱う。skin tone、ZWJ family / profession、regional flagを1 emoji cluster = weight 2として扱い、URLは23へ置換する。`x.com` / `twitter.com`のstatus URLは、`quote_tweet_id`を使わなくても公開時にquote cardを生成するため、`UNDECLARED_QUOTE_TARGET`で既定拒否する。この0.x capabilityにはquoteを承認するbypass flagを置かない。platform / plan固有制約は実行時の公式仕様を確認する。

## Canonical SQLite ledger

post ledgerは最寄りの`.git` file / directory markerからworkspace rootを解決し、そのrootの`state/x-api/x-posts.sqlite3`へ固定する。Project / Agent別daily call counterも同じrootの`state/x-api/x-usage.sqlite3`へ固定する。callerの任意path、ephemeral `.tmp` JSONL、bundled scriptからの固定depthをroot根拠にしない。markerが見つからなければdatabaseを作らずfail closedにし、prepare / send出力へrootとmarker種別を残す。

- WAL、`synchronous=FULL`、`BEGIN IMMEDIATE`を使う。
- `(account_id, content_id)`と`(account_id, content_sha256)`をuniqueにする。
- account ID、app ID、content ID / hash、normalized text、approval ID、status、attempt count / time、post ID、HTTP statusを保存する。
- external POST前にtransactionで`unknown` attemptをwrite-ahead記録する。
- event tableへattempt、result、reconcileを追記する。
- `sent`は永久にduplicate拒否する。`unknown`はreconcileまで再attempt不可。`failed`または`confirmed_absent`の再attemptには新しい署名済み`approval_id`が必要で、attempt上限は2。429の`rate_limited`だけはattemptを返還し、同じ有効manifestを再利用できる。
- 同じaccountのunknownを全て解消するまで、異なるcontent ID / hashの新規sendも拒否する。
- 429は処理前拒否としてattemptを返還する。4xxは`failed`、5xx / timeout / disconnect / missing post IDは`unknown`。

SQLiteは同一host / filesystemで同じbundled scriptとdatabaseを使うprocess間排他である。SQLiteを差し替えたり別copyのscriptを実行できる敵対的caller、network filesystem、複数machineに対するglobal uniquenessは仮定しない。その構成ではdedicated gatewayとcentral databaseがsingle writerを所有し、このSkillのpolicyをgatewayで強制する。

## Reconcile

reconcileはcanonical ledgerのunknown intentだけを対象にする。

1. ledgerからaccount、hash、attempt timeを取得する。
2. `/2/users/me`で同じaccountを再確認する。
3. accountのrecent original postsを`entities`付きで最大100件取得する。
4. postの`created_at`がattemptの30秒前から5分後に入り、normalized text hashが一致するときだけ`confirmed_success`としてpost IDを記録する。X APIのtextはHTML escape(`&amp;` / `&lt;` / `&gt;`)とt.co短縮を含むため、hash照合はraw text、unescape後text、`entities.urls`のexpanded URLで復元したtextの候補すべてに対して行う。古い同文postは一致に使わない。
5. URLを含まない本文に限り、errorのない取得timelineの最古・最新時刻がattempt timeを挟み、その範囲に一致postがなければ`confirmed_absent`とする。t.co展開が元textを完全復元できるとは限らないため、URL入り本文は自動では不在確定しない。
6. window coverageを証明できなければ`unresolved`を維持する。

`failed`または`confirmed_absent`だけが、新しい`approval_id`を持つ署名済みmanifestによる再attemptを許す。timelineのempty response、partial error、URL変換、狭いwindowを「投稿なし」と推測しない。

## Manual resolve

reconcileを繰り返しても証明できない`unknown`(典型例はURL入り本文のtimeout)は、放置すると同一accountの新規sendを恒久停止させる。この場合だけ、`resolve`コマンドを唯一の正規脱出路として使う。

- 権限条件はgateway-owned `X_API_MANIFEST_SIGNING_KEY`の保持である。一般Agentには鍵を渡さないため実行できない。
- 実postの有無をX UIなど帯域外で確認し、その証拠を必須`--reason`へ記述する。
- 実在を確認したら`--outcome sent --post-id <実ID>`で記録し、以後は永続duplicate拒否になる。
- 不在を確認したら`--outcome confirmed_absent`で記録し、新しい署名済み`approval_id`による再attemptだけを許す。
- どちらも`manual-resolve` eventとしてoutcome / reasonをevent tableへ監査記録する。
- `unknown`以外のintentには適用できない。SQLite fileの直接編集・削除・差し替えでこの手順を代替しない。

## Secrets and transport

- OAuth secret / tokenをmanifest、SQLite、error、trace、stdoutへ保存しない。
- OAuth 2.0 rotation markerはsecretを含めず、結果不明時はreauthorizationを要求する。
- auth付きrequestのredirectはsame-originを含め全面拒否する。
- base URL overrideは`X_API_TEST_MODE=true`かつposting無効時のloopback testだけに限定する。
- 複数Agentの長期無人運用ではcredentialを一般Agent環境へexportせず、dedicated gateway processだけが所有する。

## Do not do

- timeout、5xx、process crash後にblind retryしない。
- SQLite fileを削除・差替えしてduplicate gateを回避しない。
- approval後に本文、account、app、budget、expiryを書き換えない。
- reply、quote（本文中のX status URLによる暗黙quoteを含む）、like、follow、DM、delete、mediaをこの0.x write capabilityへ追加しない。
- browserをfallback posting pathにしない。
