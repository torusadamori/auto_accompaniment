# 同じ録音でbasic / jazzを比較する

作業場所は `D:\GitProjects\Music\auto_accompaniment`。
1回の演奏をJSONへ保存し、入力・時刻・Velocity・テンポ・seedを共通にして比較します。
録音にはNote On/Off（velocity=0のNote Onを含む）、ペダル等のControl Change、
Pitch Bend、Aftertouchも保存します。Standard MIDI Fileの読み書きには対応していません。

## 1. 録音

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main record-melody --input "MIDIFlex4 1" --output-file test_melody_sync.json --tempo 120 --count-in-bars 2 --click
```

2小節（8拍）のクリックを聴き、**次の高いクリックから**演奏します。
120 BPMならクリック間隔は0.5秒。1拍目は高いウッドブロック、2〜4拍目は低いウッドブロックです。
カウントイン後の小節頭がbeat 0 / time 0になり、再生側の小節頭と一致します。
最初の入力ノートへの時刻の寄せ直しやクオンタイズは行いません。
カウントイン中の入力は破棄し、終了後の入力の相対時刻・Velocity・Note Offを保存します。
`--click` を省くと、録音開始の1拍目を鳴らした後はクリックを止めます。
クリック出力先の既定は `Microsoft GS Wavetable Synth 0`。変更には `--output "ポート名"` を指定します。
クリックはGM打楽器ch10を使用し、録音ファイルには入りません。

演奏後、鍵盤を離して **Ctrl+C** で保存します。
自動停止には `--seconds 30` を追加します（カウントイン後から30秒）。録音中は入力を音源へ転送しません。
カウントイン中に中断した場合は0イベント・長さ0のファイルを保存します。
`--count-in-bars` の既定値は0で、`--click` も指定しなければ従来のクリックなし録音です。
既存ファイルは上書きしません。別テイクには別名を指定してください。
入力がなければ `Recorded events: 0` と表示されます。
ポート名は `python -m autoaccomp.main ports` で確認できます。

## 2. basicで再生

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main replay-melody --input-file test_melody_sync.json --output "Microsoft GS Wavetable Synth 0" --style basic --seed 1 --mute-melody
```

## 3. jazzで再生

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main replay-melody --input-file test_melody_sync.json --output "Microsoft GS Wavetable Synth 0" --style jazz --seed 1 --mute-melody
```

上の例は伴奏だけの比較です。メロディも聴くには **両方のコマンド** から `--mute-melody` を外してください。
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
- beat 0以降の休符も保存します（カウントインは含みません）。末尾は小節末まで伴奏を続け、終了時に音を解放します。
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
以前の同期なしJSONも再生できますが、その拍位相は自動補正しません。同期比較には新しく録音してください。
クリックもMIDI音源経由なので音源の発音遅延は残ります。今回の変更は録音・再生の論理的な拍の原点を揃えるものです。

## BasicにJazz要素を1つずつ追加する

`replay-melody --style basic` に次の独立オプションを追加できます。
省略した機能はOFFです。既存の `--style jazz` はそのまま残し、独立オプションとの併用はエラーにします。

| オプション | 変更するもの | 維持するもの |
| --- | --- | --- |
| `--harmony-stable` | JazzHarmonyの原則4拍・小節頭変更、強い根拠なら2拍で変更 | Basicの伴奏生成。選択コード変更による音の変化はある |
| `--jazz-voicing` | コンピングの音高を3rd/7th/9th等の近接ボイシングへ | コード推定、発音位置、音長、Velocity、ベース |
| `--smooth-bass` | ベースの音高を36〜50の近接音・アプローチ音へ | コード推定、4分音符の位置、音長、Velocity、コンピング |
| `--syncopated-comping` | 小節単位で4つのリズムをseed付きで巡回 | コード推定、Basicのボイシング、音長・Velocityの設定、ベース |

ベースとリズムは別々の乱数系列を使います。一方をONにしても他方の抽選は変わりません。
密度抑制・メロディ音域への回避・音量増強は追加しません。全4つONでも既存Jazz一式とは別の比較条件です。
smooth-bassは未来のコードを先読みせず、現在コードのルートへ半音/全音で接続するラインを使います。
シンコペーションは小節基準、Basicの既存パターンはコード変更からの経過拍基準です。
コード変更時の予約音キャンセルにより、リズム変更後は実際の発音数が変わる場合があります。

同じPowerShellで以下を順に実行します。共通引数を固定し、各行では1要素だけ変更しています。

```powershell
$abArgs = @('-m', 'autoaccomp.main', 'replay-melody', '--input-file', 'test_melody_sync.json', '--output', 'Microsoft GS Wavetable Synth 0', '--style', 'basic', '--seed', '1', '--mute-melody')

.\.venv\Scripts\python.exe @abArgs
.\.venv\Scripts\python.exe @abArgs --harmony-stable
.\.venv\Scripts\python.exe @abArgs --jazz-voicing
.\.venv\Scripts\python.exe @abArgs --smooth-bass
.\.venv\Scripts\python.exe @abArgs --syncopated-comping
```

組み合わせ例：

```powershell
.\.venv\Scripts\python.exe @abArgs --harmony-stable --smooth-bass
.\.venv\Scripts\python.exe @abArgs --harmony-stable --jazz-voicing --smooth-bass --syncopated-comping
```

起動・終了時の `Basic additions:` に有効な機能を表示します。`none` が比較基準です。
既存の送信件数・コード列・`--debug-accomp`・`--mute-melody` もそのまま利用できます。
この独立比較のCLIオプションはreplay-melody用です。実演奏のmelody-followは既存のままです。
