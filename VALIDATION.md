# 検証記録（2026-09-17）

- Windows、Python 3.12.10、Mido 1.3.3、python-rtmidi 1.5.8。
- プロジェクト内 `.venv` にインストール。
- 入力一覧: MIDIFlex4 0 / MIDIIN2 (MIDIFlex4) 1 / OVO 2。
- 出力一覧: Microsoft GS Wavetable Synth 0 / MIDIFlex4 1 /
  MIDIOUT2 (MIDIFlex4) 2 / MIDIOUT3 (MIDIFlex4) 3 / MIDIOUT4 (MIDIFlex4) 4 / OVO 5。
- ユーザー指定はMIDIFlex物理ポート1。入力 `MIDIFlex4 0` を選択。
- GS音源ポートを開いてC4のNote On/Off送信に成功。
- MIDIFlex4入力＋GS出力のthruを2秒実行、エラーなし。
- 固定進行表示を4小節実行、Cmaj7→A7→Dm7→G7を確認。
- GSへコード伴奏のみを120 BPMで4小節送信、遅れによる発音スキップ0件。
- MIDIFlex4入力とコード伴奏＋ベース出力を120 BPMで4小節同時実行、スキップ0件。
- 自動テスト7件成功。音程範囲、半音接続、ノート寿命、転送、時計、割り込み時の消音を検証。
- MIDIFlex4 0を20秒モニターしたが、その区間ではMIDIメッセージを受信しなかった。

未確認: 物理鍵盤の実Note On/Off受信、スピーカーの実音、演奏時のレイテンシ、音楽的な成立。
ポートへの送信成功と人間の聴感評価は区別する。耳での評価手順は[PC版MVP手順](docs/PC_MVP.md)参照。

## 正式リポジトリへの移行

初回の実装・上記の音源送信確認は、誤って次の別リポジトリで行った。

`C:\Users\torus\OneDrive\ドキュメント\ChatGPT\MusicStackChan-Tab5`

正式な反映先は `D:\GitProjects\Music\auto_accompaniment`。
移行前のHEADおよびorigin/mainは `882030690bec258da08f685ca851f2a6407650e3`。
元のREADMEは既存READMEを上書きせず `docs/PC_MVP.md` として取り込んだ。
既存の `.gitignore` を保持したため、cherry-pick後のSHAは元と異なる。

| 元コミット | 反映先コミット |
| --- | --- |
| 31ea3a004375cbad14fc3b9c0ed6e63da177a2ab | c7ab50141e6abc78f98b311c853fa9da57a9aeb1 |
| 97011c1a9adbca5865ddbb66f6228ef11a83ec05 | 3fb821cd4ffa51e191f2bb3bb43710b2fb3df261 |
| 34b756e2696e358d6cd1662cd6502755a59e9099 | 31593d823f2c6431b72c6eac3af2f849503fbd2d |
| f8995f5cee3d164d171db785016a6ea71c7bfaed | 0621568c339cdc261fded29e03cab7696522f55a |
| b69eb7883bf10b80ff1df8ab0f5ee8902a26ac91 | 257fce4f527e4c57223d33d1ff2f714e828a4daa |

反映先での確認:

- 専用 `.venv` にPC版依存と既存 `m3/requirements.txt` をインストール。
- `python -m unittest discover -s tests -v`: 77件中74件成功、3件スキップ。
  スキップはUnixソケット2件、シンボリックリンク作成権限1件。
- MIDIポート列挙と1小節の進行表示に成功。
- `autoaccomp/`、`tests/test_music.py`、`requirements.txt` は元の最終コミットと差分なし。
- UNO Q関連の `unoq/`、`m3/`、`scripts/`、`experiments/`、`config/` と既存テストは変更なし。
- UNO Q実機の再検証は行っていない。
