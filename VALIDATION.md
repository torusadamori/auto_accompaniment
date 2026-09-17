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

## 第二MVP followモード（2026-09-17）

- 作業ディレクトリとGitルート: `D:\GitProjects\Music\auto_accompaniment`。
- 開始HEAD: `ec4480863c30b44bffb90e23eb582c9f588b075d`。
- `python -m unittest discover -s tests`: 89件中86件成功、従来と同じ環境条件で3件スキップ。
- 第二MVPの追加12テストで、全12ルート×5種類の転回形、入力保持、次拍同期、
  C→Am→Dm→G7の両伴奏パート、転送、休止、消音、遅延とキュー上限を確認。
- `follow --input "MIDIFlex4 0" --output "Microsoft GS Wavetable Synth 0" --bars 2`
  を120 BPMで実行。ポートを開き、`Detected chord: (no notes)` を一度表示して正常終了。
  遅延による発音スキップ0件（この実行では入力コードがなく、伴奏の発音なし）。
- UNO Q関連のコード・設定・スクリプト・既存のUNO Qテストは変更なし。
- 物理鍵盤での認識、実音、演奏時の遅延と追従感は未確認。
  [第二MVPの実演テスト](docs/PC_FOLLOW_MVP.md)で評価する。

## follow入力収集・コード保持の修正（2026-09-17）

- 最初のNote Onから80msの固定収集窓を設け、窓内で離鍵された短いノートも収集に保持。
- 全離鍵・Unknownは最後の有効コードを上書きしない。新しい有効コードでのみ次拍切替。
- Held notes / Collected notes / Detected chord / Active accompaniment chordを変化時に表示。
- 93テスト中90件成功、従来と同じ環境条件の3件スキップ。
- 追加検証: 18ms間隔のNote Onと各10ms後のNote Offで、収集終了前に全離鍵しても
  C→Am→Dm→G7を認識し、次拍でコンピングとベースが送出される。
- 0/35/70msの押鍵で80ms後に確定し、後続音による窓延長がないことを確認。
- 疑似MIDI入力を実時間でFollowerへ渡し、実際のMicrosoft GS Wavetable Synth出力へ送信。
  ch2の伴奏Note Onは12件、ch3のベースNote Onは8件、遅延スキップ0件。
  全コードでDetected / Active表示が一致し、最終コードG7を離鍵後も保持。
- 上記は疑似入力による実出力確認。物理鍵盤を弾く再検証と聴感評価は未実施。
- 固定playの生成器、UNO Q関連コード・設定・スクリプトは変更なし。

## follow伴奏出力検証オプション（2026-09-17）

- `--debug-accomp` を追加。通常followは変更せず、検証時のみ変更拍をルート配置の和音と
  ルートベースで強調。send成功後のNote Onを発音単位でまとめて表示。
- 全99テスト中96件成功、従来の環境条件で3件スキップ。
- C / Dm / Em / F / Gについて、通常・検証の両モードで認識、activeコード、
  送信コンピングとベースの音程を確認。失敗・遅延スキップ・ミュート音を表示しないことも確認。
- 20ms間隔の疑似入力と早期離鍵を実時間で実行し、GS音源へ実際に送信。
  変更拍の表示は C=[60,64,67]/36、Dm=[62,65,69]/38、Em=[64,67,71]/40、
  F=[65,69,72]/41、G=[67,71,74]/43。遅延スキップ0件。
- 物理鍵盤・スピーカーを使った聴感の再確認は未実施。
- 固定playの生成器、入力転送、80ms収集、有効コード保持、UNO Q関連コードは変更なし。

## 第三MVP melody-follow（2026-09-17）

- 作業先 `D:\GitProjects\Music\auto_accompaniment`。C majorの6候補、直近4拍の履歴、
  音価・強拍・コードトーン採点、最低2拍保持、変更しきい値を実装。
- 全110テスト中107件成功、従来と同じ環境条件で3件スキップ。
- 追加11テストで採点、コード保持、履歴管理、入力スルー、次拍の両パート出力、
  既知メロディの候補制限と変更間隔を確認。既存follow/debug-accomp/playのテストも成功。
- 疑似単音メロディ `C C G G A A G F F E E D D C` を120 BPMでGS音源へ実時間送信。
  推定・伴奏コードはC→F→C、変更拍は1→8→12。入力14音、コンピング18音、ベース14音を送信。
  遅延による発音スキップ0件。これは正解コードの保証ではなく安定性・出力経路の確認。
- 初回の実ポート起動では以前の入力名 `MIDIFlex4 0` が見つからず終了。
  再列挙したところ現在は `MIDIFlex4 1`。その名前でmelody-followを1小節起動・終了できた。
  この区間では物理鍵盤の押鍵を受信しなかった。
- 物理鍵盤による演奏感・聴感評価は未確認。UNO Q関連コードは変更なし。
