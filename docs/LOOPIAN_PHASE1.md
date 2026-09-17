# Loopian Phase 1 — 人間は動き、PCは音程を決める

`loopian` は入力音をそのまま転送せず、既知の曲の文脈に沿ってリアルタイムに整形する独立モードです。
コード推定・自動伴奏・ドラムは実行しません。既存モードとBasic + harmony-stableには変更を加えていません。
ユーザー指定の音楽ルールを独自実装したもので、Loopian本体のコード移植やFLOW/QUBIT完全再現ではありません。

## 起動

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --tempo 120 --debug-loopian
```

既定曲はC major、4/4、120 BPM、各1小節の `Cmaj7 → Dm7 → G7 → Cmaj7` を繰り返します。
ポートを開き準備ができた時点が曲の先頭です。120 BPMでは2秒ごとにコードが進み、入力がなくても時計は進みます。
毎小節、現在と次のコードを表示します。曲自体や参照メロディは鳴りません。
`--bars 4` で4小節後に停止、既定の0は連続動作、Ctrl+Cで停止して音を解放します。

## Musical Context

`autoaccomp/loopian.py` の `FixedSong.at(seconds)` がTempo、Key、Bar、Beat、Current/Next chord、
参照メロディとその音域を提供します。時刻は単調増加時計に統一し、コードは実際のNote On送信時刻で選びます。
小節番号は内部0始まり、画面1始まりです。

参照メロディは4小節の固定データ `E4 G4 | F4 A4 | G4 F4 | E4 C4`（各2拍）。
初音の基準に音域C4〜A4の中央を使いますが、現在音・輪郭・リズムはコピーしません。
音程が確定した発音済みの音はコード切替で途中変更せず、次の押鍵から新コードを適用します。
`--chords` は和声テスト用の上書きで、参照メロディデータ自体は変えません。

MIDIファイル読み込みは未実装です。将来のローダーで拍単位の `MelodyNote` とコード列を構築し、
この文脈供給部分を拡張できます。推定器・伴奏器に依存しません。

## 音程ルール

| コード | PRIMARY（コードトーン） | SECONDARY（補助音） |
|---|---|---|
| Cmaj7 | C E G B | D A |
| Dm7 | D F A C | E G |
| G7 | G B D F | A E |

既定は `--tone-priority chord`。`--tone-priority flat` で従来のPhase 1と同じ音高列に戻せます。
chordでは方向に合う候補に限定し、`距離（半音） + SECONDARYペナルティ` の最小値を選びます。
ペナルティは通常1.5、強拍またはコード変更後の2音は4。同点はPRIMARY、距離、低い音の順で決定します。
通常の移動候補は5半音以内に制限し、その範囲に許可音がないときだけ最寄り距離まで広げます。
強拍はbeat 1/3の先頭0.25拍（16分音符幅）。実出力時刻で判定し、それ以外は弱拍です。
例えばCmaj7のB4からDOWNなら、弱拍はA4、強拍はG4。E4からUPならG4です。

コード変更は前回出力時のコード名と比較します。同じコードが続く小節では再発動しません。
新コードで実際に出す2音を強く優先し、RANGE_LIMITの待機中の仮選音では音数を消費しません。
STARTとUP/DOWN再配置はPRIMARYから帯域基準に近い音を選び、同距離なら3rd → 7th → Root → 5thを優先します。
SAMEは休止後も基本維持し、コード変更後2音の間だけ近いPRIMARYへの移動を許します。
デバッグには `Role: PRIMARY chord-tone / SECONDARY tension`、`Tone priority`、`Selection` を追加しています。

- 最初は参照メロディ音域の中央に最も近いコードトーン。既定曲のCmaj7ではE4（64）。入力の絶対音高には依存しません。
- 入力Note Onの差分からSTART / UP / DOWN / SAMEを判定。半音差と発音間隔も保持します。
- UP/DOWNは前回出力の上側/下側だけを候補にします。flatは最寄り、chordは上記スコアで選びます。入力の跳躍幅はPhase 1では出力の跳躍幅にしません。
- 動いている入力で同音を続けません。SAMEは前回音が有効なら同音、コード変更時は上記ルールを適用します。flatは最寄りの有効音です。
- 既定音域はMIDI **48〜96**。`--range-low` / `--range-high` で変更でき、少なくとも1オクターブ幅が必要です。
  従来の範囲は `--range-low 55 --range-high 84`。旧名 `--note-min` / `--note-max` も使えます。
- 方向側に候補がなくなったら **RANGE_LIMIT** のフレーズ境界です。単音のオクターブ折返しは行いません。
  発音中の音とサステインを解放し、60ms後に方向別の開始帯域から新しいフレーズを開始します。
  UPはLOW_MID（既定音域で60〜72）、DOWNはHIGH_MID（72〜84）。帯域中央に近いコードトーンを優先し、なければ許可音から選びます。
  指定音域の中央をM、幅の1/4をWとし、UPはM−W〜M、DOWNはM〜M＋Wへ比例調整します。音域端そのものは再配置候補から外します。
  flatでCmaj7の既定範囲ならUPは `… B6 C7 → [区切り] G4 A4 B4 …`、DOWNは `… D3 C3 → [区切り] G5 E5 D5 …`。
  DOWNも同様に下限で区切り、中域から下降を再開します。再配置そのものには音域移動があり、方向維持はフレーズ内で保証します。
- Note On間隔が既定 **700ms以上** なら **INPUT_PAUSE** として再開入力の方向別帯域へ再配置します。この場合、追加の60ms待ちはありません。
  `--phrase-gap-ms 400` / `600` / `800` など正の有限値で調整可能。SAMEなら帯域へ戻さず直前出力に最も近い許可音を保持します。
  大きな入力ジャンプ単独では再配置せず、例えば67→48も下側の許可音へ進めます。
- デバッグには `Phrase boundary detected`、`Reason: RANGE_LIMIT / INPUT_PAUSE`、`Direction`、`Rebase zone: LOW_MID / HIGH_MID / PREVIOUS`、`Phrase rebase` を表示します。

## タイミング

既定は `--grid 16 --timing-strength 0.5 --timing-window-ms 30`。
最寄りの1/16グリッドが未来30ms以内にある入力だけ、そこまでの時間の50%を遅らせます（追加遅延最大15ms）。
最寄りが過去、または窓の外なら即時出力。未来へしか補正しないため、look-aheadの固定遅延は不要です。
実際の音声遅延にはOSスケジューリング・MIDIドライバー・音源の遅延も加わります。

`--grid 8` は1/8、`--grid 0` は補正なし。strengthは0〜1、windowは0〜100ms。
高速入力でも発音順を逆転させず、Note Offにも同じ遅延を加えて音価を維持します（最短1ms）。
音域端の60msフレーズ境界はグリッド補正とは独立し、`--grid 0` でも入ります。
この区切り内に複数の入力が来た場合は再開まで待機し、受信順で出力するため発音が同時刻になる場合があります。
境界で解放した旧音のNote Offは新しい音を切りません。再開時のコードで音高を確定し、ペダル状態を復元します。
デバッグには入力・予定・実送信の各時刻を表示します。長い処理停止からの厳密な音価復元は対象外です。

## MIDIイベント管理

入力channel/noteと出力voiceの対応を保持し、Note On velocity=0もNote Offとして処理します。
同じ入力鍵の重複押鍵はFIFOで解放。同じ出力音を再打鍵するときは旧voiceを止めて鳴らし直し、
旧入力の遅いNote Offが新voiceを切らないようにします。入力チャンネルは全てピアノの出力ch1に集約します。
単音ジェスチャー向けで、同時和音は受信順のジェスチャーとして処理します。

velocityとサステインCC64を保持します。CC120/123は予約音も含め全解放・ジェスチャー初期化。
音集合を逸脱させるpitch bendなど、その他のメッセージは転送しません。
イベントキュー/未解放入力はそれぞれ最大4096件で、超過は明示エラーと停止時解放。
通常停止・Ctrl+C・例外で変換音を解放し、既存ポート処理でもreset/panicを実行します。

### Windows MM入力の終了

Loopianではキュー取消・Note Off → 入力close → 出力reset/panic/closeの順で終了します。
Mido 1.3.3 / python-rtmidi 1.5.8（同梱RtMidi 5.0.0）のWindows MM入力には専用終了処理を適用します。
受信フィルター設定 → native callback解除 → 最大4096件の受信キュー破棄 → 50ms待機 → native close → delete。
Midoの `callback=None` setterはキュー用callbackを再登録するので、終了時は直接解除します。演奏中には待機を加えません。

報告された `midiInUnprepareHeader` エラーの発生箇所はSysExバッファ解放です。
文言のopenPortは[RtMidi 5.0.0のclosePort実装](https://github.com/thestk/rtmidi/blob/5.0.0/RtMidi.cpp#L2548)にある表記です。
[Microsoftの仕様](https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/nf-mmeapi-midiinunprepareheader)では、
バッファがドライバーに残っている場合も失敗するため、文言だけで安全とは判断しません。
完全なnativeオブジェクト破棄を `is_deleted` で確認できた既知の例外だけ正常終了扱いにします。
`is_port_open()` はnative close前にPython側フラグが変わるため安全性の証明には使いません。
その他のエラーや解放未確認の同エラーは伝播します。50ms待機は競合を減らす対策で、ドライバー内部の失敗原因を確定・解消したという保証ではありません。

## 実鍵盤A/B比較

同じUP/DOWNジェスチャーを以下の順で比較します。各実行はCtrl+Cで終了します。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --tempo 120 --debug-loopian --grid 0 --phrase-gap-ms 700 --tone-priority flat
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --tempo 120 --debug-loopian --grid 0 --phrase-gap-ms 700 --tone-priority chord
```

コード変更の色、G7のドミナント感、跳躍の自然さ、フレーズ内の方向維持を聴き比べてください。
自動テストは方向と跳躍上限を検証しますが、聴感上の改善は実鍵盤で評価が必要です。

## 従来方式（flat）の実鍵盤テスト

1. まずCmaj7を固定し、補正を切って起動します。

   ```powershell
   .\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --tempo 120 --chords Cmaj7 --grid 0 --debug-loopian --phrase-gap-ms 700 --tone-priority flat
   ```

2. C4 D4 E4 F4 G4を単音で、Note On間隔700ms未満で上昇。開始直後なら出力は **E4 G4 A4 B4 C5**。
   鍵盤の別のオクターブから始めても、同じ上昇なら同じ出力です。
3. 再起動してG4 F4 E4 D4 C4を下降。出力は **E4 D4 C4 B3 A3**。
   再起動しない場合は前回出力から続きます。同じ鍵を連打すると同音連打できます。
4. 白鍵を低いところから高いところまで、700ms未満の間隔で長く上昇。上限では短い区切りと `RANGE_LIMIT` の表示の後、LOW_MIDから新しい上昇を確認します。
   高いところから低いところへの下降も確認。`--range-low 55 --range-high 84` を追加すれば旧上限C6で再現比較できます。
   約1秒休み、UP再開はLOW_MID、DOWN再開はHIGH_MID、SAMEは直前音付近になることを確認します。
   レガート・短いタップ・サステイン操作・Ctrl+Cで音が残らず、終了エラーが出ないことも確認してください。
5. `--chords Cmaj7` を外して4小節進行へ戻します。同じ上昇/下降を繰り返し、2秒ごとのコード表示と出力音を確認します。
   特にCmaj7にないFがDm7/G7で候補になること、Dm7にないBがG7で候補になることを確認してください。
6. `--grid 0`、既定1/16、`--grid 8` を比較。補正は小さいため、デバッグ時刻でも確認できます。

## 自動テスト

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

音集合、方向、音域境界、入力音高からの独立性、タイミング補正、コード切替、Note Off対応、
短いタップ、重複・重なった押鍵、CCと停止時解放、実CLI経由の4小節進行をハードウェア境界だけ置換して検証します。
実鍵盤の音楽的な弾き心地とPC音源の音声遅延は、上記の実演で別途評価します。
