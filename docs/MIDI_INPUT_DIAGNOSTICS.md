# MIDI入力無反応の調査（2026-09-17）

## 結論と実機確認

ユーザーが鍵盤を操作している間に、`MIDIFlex4 1`（input index 1）で受信を確認した。
Mido経由のmonitorと、Midoを介さないpython-rtmidi直接ポーリングの両方でNote Onを受信。
直接診断は25秒で194イベント、すべてノートイベントだった。

実受信例（疑似入力ではない）:

```text
RAW MIDI: note_on channel=0 note=48 velocity=85 time=0
RAW MIDI: note_on channel=0 note=48 velocity=0 time=0
RAW MIDI: note_on channel=0 note=52 velocity=85 bytes=[144, 52, 85] delta=0.006000s
RAW MIDI: note_on channel=0 note=52 velocity=0 bytes=[144, 52, 0] delta=0.056000s
```

この鍵盤の離鍵はNote On velocity=0として届く（MIDI上はNote Off相当）。
診断は受信した形式を変換せず表示するので、文字列`note_off`だけを探さないこと。

**無反応だった原因は未確定。** 今回の変更前と同じopen・poll処理で受信でき、
入力修復のためのドライバ再起動・USB再接続・他プロセス終了は実施していない。
診断機能の追加が受信を直した、と断定する根拠はない。

## Git履歴による比較

比較対象は `7a3d872` と今回の調査開始HEAD `de7f30d`。
`Held notes:` 表示がGitに最初に入ったのは `7a3d872`。
ただし `Held notes: [48, 52, 55]` という過去の実機ログ自体はGitにないため、
その実演がどのコミットで行われたかを正確には特定できない。
成功ログの形式を出せる最初のコミットを比較基点としている。

| 調査項目 | 7a3d872 → de7f30d |
| --- | --- |
| midi_io.py全体 | 差分なし |
| backend | mido.backends.rtmidiのまま |
| input_port | ポート名解決後api.open_input(name)、変更なし |
| ポート選択 | 完全名優先、次に0始まりindex、変更なし |
| forward_pending | source.poll()、1バッチ最大256件、変更なし |
| callback / on_message | monitorは音楽用callbackを指定しない。followはFollower.receive。変更なし |
| monitorの受信ループ | forward_pending→sleep(0.001)、変更なし |
| iter_pending | どちらも使用していない |
| follow.py | 送信ログ・検証用ボイシング・伴奏実行部の分離。受信収集部は変更なし |
| melody_follow.py | 279383bで追加された単音推定。monitorでは呼ばれない |
| 例外処理 | mainがOSError/ValueError/RuntimeErrorを表示しexit 1。無言で握りつぶさない |

再確認コマンド:

```powershell
git log --reverse --oneline -S "Held notes:" -- autoaccomp/follow.py
git diff 7a3d872 de7f30d -- autoaccomp/midi_io.py
git diff 7a3d872 de7f30d -- autoaccomp/main.py autoaccomp/follow.py autoaccomp/melody_follow.py
```

インストール済みMido 1.3.3のRtMidi backendも確認した。
内部RtMidi callbackがParserQueueへ入れ、poll()で取り出す実装。
ノートはフィルタ対象ではない。Active SensingだけはMido側の既定フィルタで除外される。
python-rtmidi 1.5.8の利用可能APIはWindows MM。

## Windows状態

- ポート一覧: OVO 0 / MIDIFlex4 1 / MIDIIN2 (MIDIFlex4) 2。
- Windows PnPのMIDIFlex4状態はOK。
- 調査時の関連プロセス確認ではPythonや代表的なDAW名のプロセスは見つからず、MidiSrv.exeを確認。
  これは全アプリのポートハンドル所有者を特定する調査ではなく、占有不存在の証明ではない。
- MIDIFlex4のMido/直接RtMidiによるopenは両方成功。
- USB認識順で末尾番号が以前の0から1へ変わった記録がある。
  永続IDとして保存せず、その都度一覧と照合する。
- OVOやMIDIFlexの別入力は今回実演受信の確認対象にしていない。

## 再発時の実行手順

他のmonitorをCtrl+Cで止め、各コマンドを**同時ではなく順番に**実行する。

```powershell
Set-Location D:\GitProjects\Music\auto_accompaniment
.\.venv\Scripts\python.exe -m autoaccomp.main ports
.\.venv\Scripts\python.exe -u -m autoaccomp.main monitor --input "MIDIFlex4 1" --seconds 30
.\.venv\Scripts\python.exe -u -m autoaccomp.main raw-monitor --input 1 --seconds 30
```

どちらも名前またはindexを受け付ける。raw-monitorはRtMidi自身の一覧と実際のindexを表示。
`--seconds` 省略時はCtrl+Cまで待つ。MIDI OUTへは何も送らない。

- 起動時: 実際のポート名・index・backend/API・open成功・待機状態。
- 受信時: 即時flush付きRAW MIDI。raw-monitorは生バイトと到着間隔も表示。
- 5秒ごと: 未受信または総イベント数・ノート数・最終イベントからの時間。
- 終了時: 受信数とノート数。0件ならopen成功と受信成功の違いを明示。
- open/poll失敗時: 例外型と内容を表示して異常終了。リソースは終了時に閉じる。

直接診断は `rtmidi.MidiIn → get_ports → open_port(index) → get_message()` を使用。
Midoのopen/queue/pollやforward_pending、コード認識、伴奏エンジンは通らない。
SysEx・Clock・Active Sensingも含めて受信する。

| 結果 | 次の切り分け |
| --- | --- |
| 両方ノート受信 | 入力経路はその時点で正常。伴奏側の検証へ進める |
| rawのみ受信 | 同じindex・API・同じ演奏条件か確認し、Mido側を調べる |
| 両方open成功・0件 | PCへイベントが届いた証拠なし。USB・MIDIFlexルーティング・ドライバ・他アプリを調べる |
| open失敗 | 例外内容、現在の一覧、アプリのポート占有を確認 |
| Clockなどだけ受信 | 接続はあるが鍵盤ノート未確認。Note On/Off件数を確認 |

両方0件だけでWindows故障や占有と断定はできない。必ず観測中に鍵盤を弾き、
どのコネクタのLEDが点滅したかと、診断の実ポート名/indexを対応させる。
