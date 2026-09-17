# 同じ録音でbasic / jazzを比較する

作業場所は `D:\GitProjects\Music\auto_accompaniment`。
1回の演奏をJSONへ保存し、入力・時刻・Velocity・テンポ・seedを共通にして比較します。
録音にはNote On/Off（velocity=0のNote Onを含む）、ペダル等のControl Change、
Pitch Bend、Aftertouchも保存します。Standard MIDI Fileの読み書きには対応していません。

## 1. 録音

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main record-melody --input "MIDIFlex4 1" --output-file test_melody.json --tempo 120
```

`Recording ...` が出たら一度だけ演奏し、鍵盤を離して **Ctrl+C** で保存します。
自動停止には `--seconds 30` を追加します。録音中は入力を音源へ転送しません。
既存ファイルは上書きしません。別テイクには別名を指定してください。
入力がなければ `Recorded events: 0` と表示されます。
ポート名は `python -m autoaccomp.main ports` で確認できます。

## 2. basicで再生

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main replay-melody --input-file test_melody.json --output "Microsoft GS Wavetable Synth 0" --style basic --seed 1
```

## 3. jazzで再生

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main replay-melody --input-file test_melody.json --output "Microsoft GS Wavetable Synth 0" --style jazz --seed 1
```

伴奏だけを聴くには **両方のコマンド** に `--mute-melody` を付けてください。
入力はミュート時も同じ推定器に渡ります。コンピングとベースの生成結果は変わりません。
送信ノートの表示には `--debug-accomp` を追加できます。
この再生コマンドではデバッグ表示による音色・ボイシングの変更はありません。

## 比較条件とログ

- 同じJSON・seed・オプションを使い、`--style` だけを変えます。
- 両方ともC major・4/4・progression-awareを使用し、既存のMelodyFollower、
  推定器、basic/jazz伴奏生成器、スケジューラを再利用します。
- テンポは録音ファイルから読みます。任意の `--tempo` は伴奏時計だけの変更です。
  録音されたメロディの再生速度は変えません。指定する場合は両方に同じ値を使ってください。
- メロディch1 Piano、コンピングch2 Piano、ベースch3 Acoustic Bass。
  両スタイルとも同じ音色・CC7/CC11設定で開始します。
- 録音開始前後の休符も保存します。末尾は小節末まで伴奏を続け、終了時に音を解放します。
- 再生前に録音時刻を使って伴奏イベントを確定します。同時刻の入力は保存順に処理し、
  拍境界と同時なら入力を先に処理します。PCの待機時間の揺れはコード推定に影響しません。
  ただし物理MIDI送信にはWindowsや音源の遅延があります。

終了時に次の項目が出ます。

```text
Style: jazz
Chord changes: 2 (initial chord excluded)
Comping note-ons: 21
Bass note-ons: 15
Melody note-ons: 0
Estimated progression: 1:C | 8:F | 12:Dm
Maximum MIDI send lateness: ...ms
```

数値は演奏によって変わります。Note On件数は実際に送信成功した音単位の件数です。
コード変更数は最初のコード選択を除きます。コード列の数字は録音開始を0とした拍位置です。
起動時のRecordingハッシュ・Tempo・Seedが一致することも確認してください。
Ctrl+Cで再生を止めた場合は、そこまでに出力した件数を表示します。
実送信の最大遅れが大きい場合は、重い処理を止めて再比較してください。

既存の `play` / `follow` / `melody-follow` の動作は変更していません。
