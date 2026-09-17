# PC AutoAccomp MVP

Windows / Python 3.12。プロジェクト内の仮想環境を使用します。

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
