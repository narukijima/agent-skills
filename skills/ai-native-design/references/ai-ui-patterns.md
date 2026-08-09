# AI UI patterns and state model

固定component名ではなく、利用者が理解・操作すべき意味でUIを設計する。Projectの実イベントと対応しない飾りのstateは作らない。

## State inventory

まず候補stateを列挙し、対象featureに必要なsubsetだけを採用する。

| State | 利用者に伝えること | 主なaction / behavior |
| --- | --- | --- |
| `idle` | 入力または開始を待っている | primary actionを明確にする |
| `submitting` | user inputを送信中 | 二重送信を防ぎ、cancel可否を示す |
| `queued` | 受理済みで開始待ち | 順番や待機理由が有用な場合だけ示す |
| `streaming` | responseが増分到着中 | 内容を安定表示し、停止actionを検討する |
| `reasoning` | 表示可能な分析summary / progressを生成中 | 内部思考ではなくstatusを示す |
| `tool_requested` | tool実行が提案・要求された | 対象、目的、入力scopeを示す |
| `tool_running` | toolが実行中 | agent actionとして区別し、cancel可否を示す |
| `tool_succeeded` | tool結果が確定した | summaryと必要時だけ詳細を開示する |
| `tool_failed` | toolが失敗した | 原因、影響、retry / alternativeを示す |
| `approval_required` | user判断待ち | allow / deny、scope、不可逆性を明示する |
| `cancelled` | userまたはsystemが中断した | partial outputの扱いと再開方法を示す |
| `retry` | 再試行可能または実行中 | duplicate side effectを避ける条件を示す |
| `partial_result` | 一部だけ利用可能 | 完了部分と欠落部分を分ける |
| `completed` | current runが完了した | 結果と次actionを示す |
| `empty` | data / conversation / resultがない | 次に何をすべきか示す |
| `error` | requestまたはUIが成立しない | 回復手段、保持された入力、support情報を示す |

transport state、agent state、個々のtool stateを一つのbooleanへ押し込めない。必要ならdiscriminated unionとstable IDを使い、同じeventを二重表示しない。

## Conversation and message

- user、assistant / agent、system status、tool activityの所有者を視覚とsemantic structureで区別する。
- streaming中に既読contentを再mountせず、scroll位置とselectionを壊さない。auto-scrollは利用者が上へ読みに行ったら解除する。
- message branch、edit、retryがある場合は、どのversionを見ているか分かるようにする。
- long text、Markdown、code、table、URL、citation、attachmentでoverflowと読解性を確認する。
- screen readerへtoken単位で過剰通知せず、まとまりまたは完了を適切なlive regionで知らせる。

## Prompt composer

- `idle`、`submitting`、`queued`、`streaming`のときに送信、停止、再送のどれがprimaryか明確にする。
- IME、multiline、keyboard shortcut、paste、attachment、validation、送信失敗後の入力保持を確認する。
- model / tool / mode選択は必要な場合だけ表示し、advanced optionはprogressive disclosureする。
- disabledの理由を見た目だけに依存せず伝える。

## Reasoning summary and progress

- 内部Chain of Thought、秘密の推論token、非公開promptをそのまま表示しない。
- reasoning summary、plan、進捗、execution step、tool activity、利用者に見せてよい根拠だけを表示する。
- streaming中は短いstatusを安定表示し、完了後はsummaryへ畳む。時間表示やstep数は実データがある場合だけ使う。
- disclosureの開閉、keyboard focus、reduced motion、長いsummaryを確認する。

## Tool execution and approval

- `requested`、`running`、`succeeded`、`failed`、`approval_required`を色だけでなくlabel、icon、textで区別する。
- tool名だけでなく、利用者に理解できる目的と影響を先に示す。input / raw result / logは必要時に展開する。
- secret、token、private path、不要なpayloadは表示前に除外またはredactする。
- approvalでは実行主体、対象、data scope、外部作用、可逆性、費用、期限を関係する範囲で示す。
- allow / deny後のpending、二重click、timeout、network failure、既に実行済みか不明な状態を設計する。不可逆操作を盲目的にretryしない。

## Sources and citations

- claimとsourceの対応が分かる位置に置き、title、domain、destinationを確認できるようにする。
- URLだけの長い文字列を本文へ露出しない。keyboardで到達でき、external navigationを明示する。
- source取得中、sourceなし、取得失敗、重複source、無効URLを扱う。
- citationがあることと、claimが正しいことを同一視しない。

## Artifact and generated result

- text、code、image、document、tableなどartifact種別に合うpreviewとactionを選ぶ。
- conversationとartifact workspaceのfocus、selection、version、保存状態を混同しない。
- large artifact、slow render、download failure、unsupported type、mobile fallbackを扱う。
- generated resultは確定済み、partial、stale、failedを区別し、上書きやpublishなど外部作用に確認を設ける。

## Agent progress and multi-agent

- agent identity、担当、current status、last meaningful event、result ownershipを区別する。
- concurrent agentを単一のglobal spinnerへ潰さず、同時に細かなevent logを常時露出しすぎない。
- queued / running / blocked / completed / cancelledと、親子関係またはhandoffを必要な粒度で示す。
- aggregate progressは根拠がある場合だけ数値化する。推測percentageを表示しない。

## Generative UI and workflow visualization

- model outputを任意component実行として扱わず、許可されたschema / component mapへ型付きで変換する。
- unknown component、invalid props、partial stream、version mismatchに安全なfallbackを持つ。
- workflow graphは状態、方向、分岐、approval point、failure位置をsemantic textでも理解できるようにする。
- animationは因果関係を補助する範囲に留め、reduced motionでは意味を失わない。

## Error, retry, and recovery

- user input error、network error、model error、tool error、permission errorを必要な粒度で区別する。
- message、artifact、toolのどこまで成功したかを残し、partial resultを消さない。
- retry対象と副作用を明示し、同じrequestの重複実行を防ぐ。
- recoveryできない場合は、保持されたdata、次に必要なaction、support用の安全なerror IDを示す。
