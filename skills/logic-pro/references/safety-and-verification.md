# Safety and verification

## 目次

1. 実行前ゲート
2. 一操作ループ
3. 結果分類
4. GUI fallback
5. 操作別の読戻し
6. 完成判定との境界
7. UI言語とoverlay

## 1. 実行前ゲート

writeは次の全条件を満たす場合だけ実行する。

- Logicが起動している。
- 画面がunlockedであると確認できる。
- Accessibility権限が有効である。
- 危険なmodal dialogが表示されていない。
- 利用者の依頼が当該変更を含む。
- 期待するProjectと現在Projectが一致する。
- 操作と検証用readがcapability一覧にある。
- path操作は絶対path、許可root内、許可拡張子である。

不明はfalseとして扱う。overlayとLogicのmodalを識別できない場合も停止する。接続先の実行ファイル、patch、manifestに版・commit・SHA-256制約がある場合は、実行前に照合する。

## 2. 一操作ループ

```text
fresh stateを読む
  -> Projectと前提をbindする
  -> preflightする
  -> 一操作だけ送る
  -> fresh stateを独立して読む
  -> A/B/Cへ分類する
  -> Aだけ次の操作へ進む
```

「MIDIを取り込み、音源を設定し、保存する」は三操作である。各操作の間に読戻しを入れる。bulk tool、macro、複数キー列を一操作として扱わない。

## 3. 結果分類

### A: verified_success

操作経路が成功を返し、別の状態読取でLogicのfreshな実状態が期待値と一致し、必要な出力artifactもguardが検証した。preflight fingerprintが一致する同じ要求だけを分類し、Aだけを成功として報告できる。

### B: unknown

timeout、接続断、UI変化中、stale値、証拠source不明、読戻し不一致など、実行されたかを断定できない。再送、同等GUI操作、次のwriteを行わない。まずread-onlyで状態を取り直し、曖昧なままなら停止する。

### C: confirmed_failure

未対応、明示拒否、引数エラー、前提違反など、操作が適用されなかったことが確定した。原因を返す。GUI fallbackを検討できるのはCだけである。

## 4. GUI fallback

次をすべて満たす場合だけ、一回のAccessibilityベース操作を許可する。

- MCP/adapterの結果がCである。
- GUI操作が元の利用者依頼とallowlistの範囲内である。
- Project bindingを直前に再確認した。
- 対象要素をlabel/roleで特定できる。
- 操作後の独立readbackが可能である。

座標だけのクリック、画像一致だけの対象決定、Bからのfallback、複数GUI操作の連鎖は禁止する。GUI fallbackも同じA/B/C分類を使う。

## 5. 操作別の読戻し

| 操作 | 最低限の期待証拠 |
| --- | --- |
| play / stop | `isPlaying`が期待boolと一致 |
| set tempo | Logicが返すtempoが許容誤差内で一致 |
| set position | Logicが返すpositionが正規化後に一致 |
| track select | selected trackの安定IDまたはindexが一致 |
| set instrument | 対象trackのinstrument IDが一致 |
| MIDI import | 取込前後のregion/track差分と対象MIDIに対応する新規要素 |
| save | dirty stateの解消または保存時刻など、接続先が保証する保存証拠 |
| save as | 現在Projectのpathが新規pathへ変わり、対象が存在 |
| bounce | preflightで不在を確認した同一pathに、symlinkでない通常fileが新規作成され、サイズが0より大きい。可能ならaudio metadataも検査 |

UI表示値しか取れないときは、その制限を証拠sourceへ明記する。MIDI commandの送信成功やファイルの存在だけで、Logic内の反映を保証しない。

## 6. 完成判定との境界

このSkillが保証するのは操作反映までである。次は別途検証する。

- 作曲・編曲・演奏・ミックスの良し悪し
- bounceされた音声の聴感、無音、clip、尺、sample rate
- plugin、sample、楽曲の権利
- Project全体の完成状態

操作証拠と作品品質証拠を別々に報告する。

## 7. UI言語とoverlay

Accessibility実装は座標よりAX role、identifier、valueを優先し、表示文字列を使う場合はLogicの現在言語へ対応する。日本語UIでは少なくとも「再生」「録音」「サイクル」「メトロノーム」「テンポ」「コントロールバー」を意味要素へ対応付ける。英語labelだけを前提にしない。

画面共有などのmacOS system overlayをLogicのmodalと誤認しないよう、AX ownerまたはbundle identifierを確認する。一方、Logic所有の通常dialogは危険なmodalとして停止する。ownerを判定できないoverlayは安全側に倒して停止する。
