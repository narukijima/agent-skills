# Levels — Levelから認知制約への変換

Levelは操作用のproxy labelであり、年齢の科学的な絶対基準ではない。「ELI12なら12歳の全員が理解できる」ことは保証しないし、主張しない。正本は各Levelが指す認知制約profileである。

## 認知制約 (C1〜C11)

| ID | 制約 | 内容 |
| --- | --- | --- |
| C1 | 必要な前提知識 | 理解に必要だが本文・画面が提供しない知識 |
| C2 | 語彙の難易度 | 日常語か、低頻度語・学術語か |
| C3 | 専門語の扱い | 未定義termの数と、初出時の接地方法(定義、具体例、対比) |
| C4 | 抽象度 | 具体例に接地されずに提示される一般化・原理の割合 |
| C5 | 推論の段数 | 明示されず受け手が自分で補う推論のstep数 |
| C6 | 一度に扱う概念数 | 1情報単位で同時保持する新規概念数。約4 chunkは限定条件下の成人短期記憶研究から得た警戒値であり、全年齢・全課題の上限ではない |
| C7 | 情報単位の複雑さ | 文・shot・画面あたりの構造(節の入れ子、従属関係、条件分岐) |
| C8 | 作業記憶への負荷 | C5、C6、C7と受け手の既有知識が相互作用して生じる負荷。単純な積では計算しない |
| C9 | 文脈依存性 | 離れた位置の情報を保持し続けないと理解できない構造 |
| C10 | 視覚的な即時理解性 | 読解を経ずに構造・状態が掴めるか(媒体依存) |
| C11 | 操作・判断の認知負荷 | 次の行動を決めるまでに要する推論と保持(媒体依存) |

C10とC11の媒体別の現れ方は `media-rules.md` が定義する。

## Level profiles

各profileは制約の強さの段階であり、下のLevelほどfloorが低い(=より少ない前提で理解できる)。トーンはどのLevelでも受け手の年齢ではなく用途に合わせる。表中の概念数・推論段数は一貫した生成と検証のための運用heuristicであり、各年齢の能力を示す実証済みcutoffではない。Audienceによる実測があればprofileより実測を優先する。

| Level | 前提知識 (C1) | 語彙・専門語 (C2, C3) | 抽象度・推論 (C4, C5) | 同時概念 (C6, C8) | 単位・文脈 (C7, C9) |
| --- | --- | --- | --- | --- | --- |
| ELI5 | 日常経験のみ | 日常語のみ。専門語は不可避時のみ、即時に具体物へ言い換え | 抽象化なし。全stepを明示(補う推論0〜1段) | 新規概念は1つずつ | 1文1情報。文脈保持ほぼ不要 |
| ELI8 | 日常経験+基礎的な数量・分類 | 専門語は1情報単位に1個まで、直後に具体例 | 単純な一般化のみ。推論1段 | 同時2概念 | 短文中心。直前の文脈のみ |
| ELI10 | 一般的な基礎教科の概念 | 専門語導入可(定義+例が必須) | 限定的な抽象化。推論1〜2段 | 同時2〜3概念 | 段落内の文脈保持 |
| ELI12 | 分野固有知識を要しない基礎教養 | 必要な専門語を導入可。初出で必ず接地 | 抽象原理は具体例とペア。推論2段まで | 同時3概念前後 | 節内の文脈保持。前方参照は再掲する |
| ELI15 | 一般的な中等教育の概念 | 初出定義があれば専門語を連用可 | 抽象的議論可。推論2〜3段 | 同時3〜4概念 | 章内の文脈保持 |
| Adult | 一般成人の教養・社会経験 | 他分野の専門語には接地が必要 | 推論は明示的なら制限なし | 同時4概念目安 | 文書全体。ただし構造(見出し、要約)で支援 |
| Expert | 対象分野の専門知識 | 分野内専門語は未定義で使用可 | 分野慣行に従う | 過剰なelement interactivityは依然下げる価値がある | 分野慣行に従う |

適用時の読み方:

- profileは「どこまで許されるか」の上限であり、「そこまで複雑にせよ」ではない。低い負荷で書ける箇所を意図的に難しくしない。
- 内容(intrinsicな複雑さ)は削らない。削るのは提示方法が生む余分な負荷(extraneous load)だけである。
- 一般公衆向けのproseでは ELI12前後 を有力なdefault候補にできる。ただしこれはWCAG 2.2のLevel AAA基準が複雑な文章へ補足版等を求める際に使うlower-secondary水準を、運用上の候補へ援用したものにすぎない。ELI12との科学的同値性や、全媒体への普遍的defaultを意味しない。
- 語彙頻度、教育段階、日常経験は言語・文化・地域で異なる。ある言語のreadability gradeや語彙表を別言語へそのまま移さず、対象言語とAudienceで解釈する。

## auto の解決

Levelの明示指定がない場合、次の優先順位で最低理解レベルを決め、根拠を1行記録する。

1. 利用者または上位Agentの明示指定(「専門家向け」「小学生でも」等の言い換えを含む)。
2. 想定Audience: 主要な受け手のうち、必要な情報へ到達させるべき最も低い既有知識・読解条件へfloorを合わせる。氏名、外見、属性等から年齢や能力を推測しない。
3. Medium: `media-rules.md` の媒体別default候補を使う。
4. Purpose: 教育・公共・安全・同意取得の情報は低め、分野内の技術文書は高めへ寄せる。
5. 一般default: 一般公衆向けはELI12前後を候補とする。

どの段でも解決できない場合は不明として返す(SKILL.md出力契約)。Project固有のAudience定義、KPI、ブランド文体はこのSkillへ埋め込まず、利用側からの入力としてのみ受け取る。`auto` は便利なfallbackであり、Audience調査の代替ではない。

## 根拠

保存するのは適用に必要な結論だけとし、外部資料の本文はコピーしない。Level profile自体は以下を組み合わせた運用モデルであり、単一研究がELI5〜Expertの対応表を検証したものではない。適用時に疑義があれば原典を確認する。

- [Anthropic-hosted community plugin `eli5`](https://github.com/anthropics/claude-plugins-community/tree/main/eli5): 「理解可能性を独立したSkillとして呼び出す」という発想上の先行例。本Skillはbig pictures / few wordsに限定せず、年齢labelを認知制約へ変換して媒体横断化する。このpluginは学術根拠としては扱わない。
- [Sweller, “Cognitive load during problem solving: Effects on learning” (1988)](https://doi.org/10.1016/0364-0213(88)90023-7): 問題解決が認知処理容量を消費し、schema獲得を妨げうることを示すCognitive Load Theoryの基礎。内容を削るのでなく、提示由来の不要な処理を減らす方針の根拠。
- [Cowan, “The magical number 4 in short-term memory” (2001)](https://doi.org/10.1017/S0140525X01003922): rehearsalやre-coding等を制限した条件で中心的容量が平均約4 chunkという議論。C6の警戒値には使うが、年齢別profileやUIのhard limitには使わない。
- [Digital.gov, “Principles of plain language”](https://digital.gov/guides/plain-language/principles) / [“Test for understanding”](https://digital.gov/guides/plain-language/test): 特定Audienceを起点にし、「dumbing down」と混同せず、paraphrase testやusability testで実際の理解を確認する根拠。
- [WCAG 2.2 SC 3.1.5 Reading Level](https://www.w3.org/TR/WCAG22/#reading-level): lower secondaryより高度な読解を要する場合に補足内容または平易な版を求めるLevel AAA基準。一般向けELI12前後の候補を考える参考であり、ELI12との同値性は主張しない。
- [W3C, “Making Content Usable for People with Cognitive and Learning Disabilities”](https://www.w3.org/TR/coga-usable/): 明確な語、step、control、結果、短い情報単位、記憶に依存しないprocess、実利用者testを扱う補足guidance。W3C RecommendationではなくWorking Group Noteであり、WCAG適合要件そのものではない。
- [Mayer, “Multimedia Learning”, 3rd ed. (2020)](https://doi.org/10.1017/9781316941355): coherence、signaling、segmenting等を図解・映像の制約へ使う根拠。原理には適用条件があり、全媒体へ機械的に同じ形で強制しない。
- [“Moving Beyond Readability Metrics for Health-Related Text Simplification” (2016)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5044755/): 一般的なreadability formulaが概念、結束性、構成や実理解を十分に捉えないという査読研究。scoreを補助信号に留める根拠。
