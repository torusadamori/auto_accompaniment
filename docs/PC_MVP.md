# PC AutoAccomp MVP

Windows / Python 3.12。プロジェクト内の仮想環境を使用します。
以下のコマンドはリポジトリルート `D:\GitProjects\Music\auto_accompaniment` で実行してください。
UNO Q版とは独立したPC用アプリです。ルートの `requirements.txt` はPC版用で、
UNO Q版は従来どおり `m3/requirements.txt` を使用します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m autoaccomp.main ports
.\.venv\Scripts\python.exe -m autoaccomp.main monitor --input 0
.\.venv\Scripts\python.exe -m autoaccomp.main test-tone --output 0
.\.venv\Scripts\python.exe -m autoaccomp.main thru --input 0 --output 0
```

番号は `ports` の結果で選びます。完全なポート名も指定可能。停止は Ctrl+C。
`monitor` / `thru` に `--seconds 10` を付けると自動停止します。
Note On（velocity=0を含む）/ Note Off / Velocity を保持し、メロディはMIDI ch1へ転送します。
サステイン、ピッチベンド、アフタータッチも転送します。終了時にはサステイン解除と消音を送信します。
音源の音色を保つため入力のProgram ChangeとSysExは転送しません。

PCソフト音源が直接ポートを公開しない場合は、loopMIDIでポートを作り、
Pythonの出力と音源の入力をそのポートに合わせます。音源から同じポートへ戻す設定は避けてください。
外部機器は今回対象外なので、出力にはPC音源かその仮想ポートを選択してください。

実装のAPI参照: [Mido](https://mido.readthedocs.io/en/stable/ports/index.html)、
[python-rtmidi](https://spotlightkid.github.io/python-rtmidi/)。

## 第一MVPを演奏する

このPCで確認した入力は `MIDIFlex4 0`（MIDIFlex物理ポート1）、
PC音源は `Microsoft GS Wavetable Synth 0` です。
まず音量を適度に設定し、以下を実行して鍵盤を弾いてください。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main play --input "MIDIFlex4 0" --output "Microsoft GS Wavetable Synth 0"
```

`Cmaj7 → A7 → Dm7 → G7` を1コード1小節、4/4、120 BPMで繰り返します。
メロディはch1、控えめなピアノ伴奏はch2、アコースティックベースはch3。
3パートを受けられるマルチティンバー音源を使用してください。MIDI ch10は使用しません。
コードはルートを省いた3・5・7度を小さな移動でつなぎ、2小節の固定コンピングパターンを使います。
ベースは4分音符でルート・3度・5度・次コードのルートへの半音アプローチです。
乱数・コード推定・演奏に反応する処理はまだありません。

```powershell
# Step 3: 音を出さず進行だけ確認
.\.venv\Scripts\python.exe -m autoaccomp.main progression --bars 8
# Step 4: コード伴奏のみ（鍵盤入力を付ける場合は --input 0）
.\.venv\Scripts\python.exe -m autoaccomp.main play --output 0 --no-bass --bars 8
# Step 5: ベースのみを評価
.\.venv\Scripts\python.exe -m autoaccomp.main play --output 0 --no-comping --bars 8
# 別進行の例（Autumn Leaves冒頭に相当する8小節、E minor）
.\.venv\Scripts\python.exe -m autoaccomp.main play --input 0 --output 0 --tempo 100 --chords Am7 D7 Gmaj7 Cmaj7 F#m7b5 B7 Em7 Em7
```

`--bars 0`（既定値）は無限ループ。コードはmaj7 / m7 / 7 / m7b5とシャープ・フラットに対応。
現状はすべて1小節単位です。MIDIファイルの再生・出力は実装対象外です。

## 耳での評価

1. `test-tone` でPC音源が聞こえることを確認。
2. `monitor` でNote On / Note Off / Velocityの受信を確認。
3. `thru` で鍵盤と発音の遅れ、サステイン、離鍵時の消音を確認。
4. `play --no-bass` で音域・音量・コンピングがメロディを邪魔しないか確認。
5. `play` でベースの流れ、ループ末尾のG7→Cmaj7、ジャズらしさを評価。
6. 数分演奏し、一緒に演奏している感じ、飽きやすさ、タイミングの違和感をメモ。

音が聞こえない場合はWindowsの出力先・音量ミキサーとソフト音源の入力設定を確認。
ポートを開けない場合は同じポートを利用中のDAW等を閉じ、`ports` を再実行してください。
入力ポートが開いたことだけでは、物理鍵盤から信号が届いたとは判定できません。
GS音源で演奏の遅れが気になる場合は、使用するソフト音源とオーディオ設定を見直してから
アルゴリズムのタイミングを評価してください。

## 構成と検証

`chord_progression.py` / `comping.py` / `walking_bass.py` はMIDIポートに依存しません。
生成結果は拍単位の `Note`。`transport.py` の単一の単調時計で進行と発音を同期し、
`scheduler.py` でMIDI化、`midi_io.py` でポートに接続します。
`config.py` にテンポ・進行・チャンネルの既定値を置いています。
大きな処理停止時は古い発音をまとめて出さず、1/8拍以上遅れたNote Onをスキップし、
Note Offは処理します。正常終了時にスキップ件数を表示します。
コンソールへの大量出力は演奏タイミングに影響するため `play` は小節表示のみです。
終了時は選択した出力の全チャンネルに消音を送るため、評価用の独立した音源／ポートを推奨します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自動テストはコード構成、ベースの接続、複数ループのNote On/Off対応、
処理遅延時の消音・発音スキップ、MIDI転送、時計の進行を確認します。
これらは実際の音の良さやオーディオ出力レイテンシを保証するものではありません。
