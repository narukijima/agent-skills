# Skill catalog

各Skillの発動条件は個別の `SKILL.md` frontmatterが正本です。この一覧は人間向けの入口であり、Skill本文の複製を持ちません。`_template/` は新しいSkillを作るための雛形で、Skillとして発動させません。

| Skill | 状態 | 用途 |
| --- | --- | --- |
| [`ai-native-design`](ai-native-design/SKILL.md) | active | AI-native UIの既存Project調査、source探索、選定、統合、品質検証 |
| [`seo`](seo/SKILL.md) | active | 検索・AI可視性を実測し、crawl / index / content / schema / pSEOの原因修正を再検証 |
| [`sns-api`](sns-api/SKILL.md) | active | X（URL引用・画像・動画・GIF）/ YouTube / Facebook Pages / Instagram Professional / Threads公式APIの署名manifest・canonical ledger実行 |

## 新しいSkill

1. `skills/_template/` をコピーして、ディレクトリ名とfrontmatterを置き換える。
2. `SKILL.md` に発動条件、Knowledge参照、手順、出力契約、禁止事項を書く。
3. 再利用が安定した決定処理だけを `scripts/` に置く。
4. `bash tools/validate-skills.sh` と対象テストを実行する。
5. 必要な利用側Agentへだけ `bash tools/import-skill.sh <skill-name> --target <agent-directory-root>` で取り込む。

Skillを追加したら、個別ディレクトリとこの一覧を同じ変更単位で更新し、`bash tools/validate-skills.sh` を実行します。
