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
ポートへの送信成功と人間の聴感評価は区別する。耳での評価手順はREADME参照。
