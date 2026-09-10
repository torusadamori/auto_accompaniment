# Auto Accompaniment

半自動アコーディオンに、リアルタイムのジャズ伴奏生成機能を追加するプロジェクトです。

主旋律は既存のMIDIデータから半自動アコーディオンのソレノイドを駆動して生音で演奏します。伴奏は約40 cmのタッチバーと主旋律MIDIを統合して生成し、Yamaha MU80へMIDI出力してスピーカーから再生します。

## コンセプト

狙いは、固定パターンを流す「自動伴奏」ではなく、曲を知っている気心の知れたピアノ伴奏者のような振る舞いです。

- 主旋律が忙しいときは伴奏を薄くする
- 主旋律が長音・休符に入るときはフィルを入れる
- 主旋律の上昇・下降や着地点を見て、伴奏の方向を合わせる
- タッチバーをポンと触ればコード/コンピングを指示できる
- タッチバーを滑らせれば、コード/スケールに沿ったアルペジオやランを生成できる
- タッチ速度や方向を表現量に使う
- 毎回完全に同じではないが、音楽的ルールを優先し、ランダム性は補助的に使う

## 想定ハードウェア

中心ボードは **Arduino UNO Q 2GB** を第一候補とします。

UNO Qは、Debian Linuxを動かすQualcomm Dragonwing QRB2210 MPUと、リアルタイム制御用STM32U585 MCUを同一ボードに持つため、今回の用途に適しています。

### Linux / MPU側

- オンボードBluetooth経由のBLE MIDI受信
- 主旋律MIDIのlook-aheadバッファ
- コード/拍/フレーズ解析
- Jazz Engine
- タッチバー情報との統合
- MU80向け伴奏MIDIイベント生成
- ログ、設定、デバッグUI

### STM32U585 / MCU側

- 半自動アコーディオンのソレノイド制御
- タッチバーの低遅延サンプリング
- 時刻指定されたイベントの決定論的実行
- 必要に応じてMU80への物理DIN MIDI送出
- All Notes Off / ソレノイド強制OFFなどの安全処理

> UNO QのオンボードBluetoothはMPU/Linux側の無線系を利用する前提とし、STM32が直接BLEを受信する構成にはしません。

## タイミングの基本思想

主旋律MIDIに固定遅延 `LOOKAHEAD_MS` を入れます。初期値は300 ms程度を候補とし、0 / 300 / 500 / 1000 msを比較します。

```text
BLE MIDI input
      |
      +--> Linux: melody analysis / Jazz Engine
      |
      +--> scheduled accordion event at t + LOOKAHEAD_MS

Touch Bar ----> STM32 ----> Linux Jazz Engine
                        (current audible-time gesture)

Linux Jazz Engine ----> accompaniment MIDI events ----> MU80
STM32 scheduler ------> solenoids --------------------> Accordion acoustic sound
```

固定遅延の目的は計算時間の確保ではなく、Jazz Engineが主旋律の未来を先読みできるようにすることです。ルールベースの伴奏生成自体は非常に軽量です。

### タッチバーは遅延させない

タッチバー操作は「今聞こえている音楽」に対する演奏者の意思なので、原則として `LOOKAHEAD_MS` をもう一度加えません。

例えば300 msの主旋律バッファがある場合、タッチした瞬間にLinux側は、現在聞こえている主旋律に加えて約300 ms先までの主旋律イベントをすでに知っています。この未来情報を使い、次の8分音符/16分音符などへ伴奏を量子化して返します。

## タッチバー

半自動アコーディオンに後付けできる約40 cmの定規状デバイスを想定しています。

- 導電性フィラメントを利用した3Dプリント鍵盤/タッチ面
- 初期試作は24〜32程度の離散タッチ領域でもよい
- 位置、方向、速度、タッチ開始/終了を抽出
- 将来的に連続位置センサー化も検討

概念イベント例:

```text
TouchEvent {
    position: 0.0..1.0,
    direction: -1 | 0 | +1,
    speed: 0.0..1.0,
    active: bool,
    timestamp_us: uint64
}
```

## Jazz Engine

初期版はAI/LLMではなく、低遅延で説明可能なルールベースとします。

参考にする考え方:

- 現在コード/スケールへのnearest-note snapping
- 直前音とタッチ方向を考慮した同音連続回避
- コードトーン、7th、9th、11th、13thの段階的利用
- passing note / approach note / enclosure
- 主旋律密度に応じた伴奏密度制御
- 長音・休符でのfill生成
- velocity / timingの軽いhumanization
- タッチ速度に応じたランの音数・密度変更

Loopianからは特に `FLOW`、`note_translation`、コード/スケールテーブル、humanizationの設計思想を研究対象とします。

Reference: https://github.com/hasebems/Loopian_Rust

Loopian_RustはMIT Licenseです。コードを直接再利用する場合は、ライセンス表記・著作権表示を適切に保持します。

## 開発文書

- [Concept Review](docs/CONCEPT_REVIEW.md) — Claude等に設計レビューを依頼するための文書
- [Development Specification](docs/DEVELOPMENT_SPEC.md) — 実装仕様、時間モデル、モジュール構成、マイルストーン
- [CLAUDE.md](CLAUDE.md) — Claude Code向けプロジェクト指示

## ローカル開発パス

```text
D:\GitProjects\Music\auto_accompaniment
```

GitHub:

https://github.com/torusadamori/auto_accompaniment

## 現在の優先順位

1. UNO Q上でBLE MIDI受信を確認
2. 固定look-ahead付きMIDIイベントキューを実装
3. STM32で半自動アコーディオンを時刻指定駆動
4. タッチバー入力を統合
5. 最小Jazz Engineを実装
6. MU80でピアノ伴奏を出力
7. 実演しながら伴奏ルールを改善
