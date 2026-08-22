# Skill catalog

各Skillの発動条件は個別の `SKILL.md` frontmatterが正本です。この一覧は人間向けの入口であり、Skill本文の複製を持ちません。`_template/` は新しいSkillを作るための雛形で、Skillとして発動させません。

| Skill | 状態 | 用途 |
| --- | --- | --- |
| [`ai-native-design`](ai-native-design/SKILL.md) | active | AI-native UIの既存Project調査、source探索、選定、統合、品質検証 |
| [`comprehension-level`](comprehension-level/SKILL.md) | active | ELI5〜Expert / autoのLevelを認知制約へ変換し、媒体横断で最低理解レベルを制御・検証 |
| [`origen`](origen/SKILL.md) | active | Human Root、Structural/Content Provenance、STANDARD/STRICT ORIGINを検証してpublish-readyを判定 |
| [`seo`](seo/SKILL.md) | active | 検索・AI可視性を実測し、crawl / index / content / schema / pSEOの原因修正を再検証 |
| [`sns-algorithm`](sns-algorithm/SKILL.md) | active | 6 SNSの推薦・ranking・searchをsurface別の公式根拠から分析し、仮説と実験へ変換 |
| [`sns-api`](sns-api/SKILL.md) | active | X media/URL引用、YouTube resumable、Meta stage recoveryを含む5 SNS公式APIの署名manifest・canonical ledger実行 |

## 新しいSkill

1. `skills/_template/` をコピーして、ディレクトリ名とfrontmatterを置き換える。
2. `SKILL.md` に発動条件、Knowledge参照、手順、出力契約、禁止事項を書く。
3. 再利用が安定した決定処理だけを `scripts/` に置く。
4. この一覧へ登録し、`bash tools/validate-skills.sh` と `python3 -m unittest discover -s tests` を実行する。
5. 必要な利用側へだけ `bash tools/import-skill.sh <skill-name> --target <consumer-root>` で取り込む。
