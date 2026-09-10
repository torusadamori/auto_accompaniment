# Concept Review Brief

この文書は、Auto Accompanimentプロジェクトの現時点のコンセプトを第三者（特にClaude / Claude Code）にレビューしてもらうためのレビュー依頼書です。

## 1. 目標

半自動アコーディオンを主旋律楽器として使い、主旋律MIDIと演奏者のタッチバー操作をリアルタイムに統合して、Yamaha MU80からジャズ系のピアノ伴奏を生成する。

単純な自動伴奏ではなく、次のような「曲を知っている気心の知れた伴奏者」を目標とする。

- 主旋律の邪魔をしない
- 主旋律が忙しいときは引く
- 長音や休符に入るとフィルを返す
- 主旋律の向き・着地点を意識してランを作る
- 演奏者がタッチバーで伴奏者にリアルタイムの合図を出せる
- 同じ入力でも少しずつ違うが、音楽的整合性は維持する

## 2. システム全体像

第一候補ハードウェアはArduino UNO Q 2GB。

UNO Qには以下の2つの計算系がある。

1. Linux/MPU: Qualcomm Dragonwing QRB2210 + Debian Linux
2. MCU: STM32U585 + Arduino Core on Zephyr

想定責務は以下。

### Linux / MPU

- BLE MIDI入力（オンボードBluetooth）
- 入力主旋律のlook-aheadバッファ
- 曲/コード/拍の状態管理
- 主旋律フレーズ解析
- Jazz Engine
- タッチバーイベントとの統合
- MU80向けMIDIイベント生成
- 設定、ログ、デバッグUI

### STM32U585 / MCU

- 半自動アコーディオンのソレノイド駆動
- タッチバーのセンサー読み取り
- タッチ位置/速度/方向の低レベル処理
- Linuxから渡されたtimestamp付きイベントの決定論的実行
- watchdog / panic / all-solenoids-off
- 必要ならMU80への物理DIN MIDI送信も担当

Bluetooth無線はUNO QのMPU/Linux側に接続されるため、BLE MIDI受信もLinux側で行う前提。

## 3. 主旋律の固定look-ahead

### 発想

BLE MIDIで主旋律イベントを受信しても、アコーディオンを即時には鳴らさず、一定時間 `LOOKAHEAD_MS` だけ遅らせる。

例:

```text
wall time      0 ms                        300 ms
               |-----------------------------|
BLE NOTE ON -> Linux receives             Accordion sounds
               |                           |
               + analyze future melody     + solenoid fires
               + generate accompaniment    + MU80 accompaniment
```

初期候補:

- 0 ms
- 300 ms
- 500 ms
- 1000 ms

実運用では300〜500 ms程度を第一候補とする。

### 目的

計算時間の確保ではない。Jazz Engineの計算そのものは軽量である。

固定遅延を導入する理由は、伴奏生成器に「未来の主旋律」を見せるため。

たとえば現在聞こえている音がGで、その300 ms後に長いEへ着地することをエンジンが知っていれば、ピアノをEへ向かう短いランにできる。

## 4. タッチバーの時間軸

ここが設計上もっとも重要なレビュー対象。

### 原則

主旋律MIDIはlook-aheadのため遅延する。

しかしタッチバーは、演奏者が「今聞こえている音」に対して出す意思表示なので、タッチ操作そのものに同じ `LOOKAHEAD_MS` を再適用しない。

```text
BLE melody input ---- look-ahead buffer ----> audible accordion
                          |
                          + future melody known to engine

Audible music <------ performer hears this
                          |
Touch gesture ------------+
                          |
                    Jazz Engine
                          |
                     next grid
                          |
                    MU80 accompaniment
```

タッチ入力は原則即時、または次の8分/16分音符グリッドへ短く量子化して返す。

### 期待する利点

300 msの主旋律look-aheadがあるなら、タッチした瞬間にエンジンは現在音だけでなく、これから300 ms程度に来る主旋律も知っている。

したがって、演奏者が右方向へ高速スライドしたとき、単なる上行アルペジオではなく、直後の主旋律着地点に向かう上行ランを生成できる。

### レビューしてほしい点

- この2時間軸モデルは破綻しないか
- タッチ発音を即時とすべきか、次の音楽グリッドへ量子化すべきか
- 主旋律と伴奏の同期基準をwall-clockにするか、musical tickにするか
- tempo change時にlook-aheadをms固定にするかbeat単位にするか

## 5. タッチバー

半自動アコーディオンに後付けする約40 cmの定規状コントローラ。

QUBITのような円形・高密度デバイスは目指さず、簡単に製作・交換できることを優先する。

### 初期案

- 約40 cm
- 3Dプリント
- PLA/PETGベース + 導電性フィラメントのタッチ面
- 24〜32区画程度から試作
- 必要に応じてMPR121など複数の静電容量タッチコントローラ

### 取得したい情報

```text
position    0.0 .. 1.0
speed       0.0 .. 1.0
direction   -1 / 0 / +1
active      true / false
timestamp   monotonic time
```

### 想定ジェスチャ

- tap: コンピング/コード打音
- slow slide: 穏やかなアルペジオ
- fast slide: ラン/フィル
- right slide: 上行を優先
- left slide: 下行を優先
- hold: 将来的に密度/強度/サステイン等へ割当可能

## 6. Jazz Engine

初期版はAIモデルを使わず、説明可能なルールベースとする。

### 基本処理

```text
Touch position
    -> provisional pitch
    -> current chord/scale
    -> nearest legal note
    -> direction-preserving correction
    -> phrase grammar
    -> velocity/timing humanization
    -> MIDI event(s)
```

### 主旋律も利用する

入力はタッチバーだけではない。

```text
Accompaniment = f(
    current chord,
    recent melody,
    future melody buffer,
    melody density,
    upcoming rests/long notes,
    touch position,
    touch speed,
    touch direction,
    style parameters
)
```

### 伴奏者らしいルール例

- 主旋律note densityが高い -> piano densityを下げる
- 主旋律長音 -> fill opportunityを上げる
- upcoming rest -> fill opportunityを上げる
- 主旋律が上昇中 -> 同方向/反方向をスタイル設定で選択
- 次の着地点が分かる -> approach targetとして利用
- 同じ音が繰り返される -> chord/scale内で方向に合う隣接音へ移動

## 7. Loopianから参考にしたい部分

Reference:
https://github.com/hasebems/Loopian_Rust

Loopianの全体システムを移植する意図はない。

参考にしたいのは主に以下。

- `FLOW`: タッチ位置/MIDI入力を演奏音へ変換する発想
- `note_translation.rs`: chord/scaleに対するnearest-note変換
- arpeggio時の直前音・方向を考慮した変換
- beatに応じたvelocity補正
- 小さなランダムvelocity humanization
- 小さなtiming dispersion
- chord/scale table表現

Loopian_RustはMIT License。直接コードを利用する場合は著作権表示とライセンス条件を保持する。

## 8. MU80の位置づけ

Jazz Engineは音声波形を生成しない。

Linux側でMIDIイベントを作り、Yamaha MU80を外部マルチティンバー音源として使用する。

初期版:

- Piano accompanimentのみ

発展版:

- Piano
- Bass
- Drums
- optional strings/other voices

主旋律は半自動アコーディオンの生音を主とし、MU80の伴奏はスピーカーから出す。

## 9. 未決定事項

Claudeには特に次をレビューしてほしい。

1. UNO Q 2GB一枚構成で十分か。Raspberry Pi + M5Stamp構成に明確な優位性があるか。
2. LinuxでBLE MIDIを受信し、RPCでSTM32へtimestamp付きイベントを渡す構成は堅牢か。
3. Linux->STM32間のイベントスケジューラはどの粒度・プロトコルがよいか。
4. MU80への物理MIDI送信をLinuxが直接行うか、STM32に送信イベントを予約するか。
5. 固定look-ahead 300〜500 msは音楽体験上有効か。よりよい方式はあるか。
6. タッチ操作を遅延させず、未来主旋律だけ参照する二時間軸は自然か。
7. PythonでJazz Engineを先に作るか、Rust/C++で最初から実装するか。
8. STM32側のソレノイド安全処理で不足しているものは何か。
9. BLE切断、Linux停止、RPC遅延時のfail-safeをどう設計するべきか。
10. 24〜32区画の導電性フィラメントタッチバーは妥当か。より簡単で安定するセンシング方法はあるか。
11. Loopianから参考にするアルゴリズム範囲は適切か。
12. この設計で、演奏者にとって「伴奏と共演している」感覚を損なう可能性がある点は何か。

## 10. Claudeへの依頼文

以下をそのままClaudeに渡してよい。

---

このリポジトリの `README.md`, `docs/CONCEPT_REVIEW.md`, `docs/DEVELOPMENT_SPEC.md` を読んで、実装を始める前にアーキテクチャレビューをしてください。

特に、Arduino UNO QのLinux/STM32の責務分離、BLE MIDI入力、固定look-aheadによる主旋律先読み、タッチバーだけを現在のaudible timeとして扱う二時間軸、MU80 MIDI出力の同期方法を重点的に検証してください。

レビューは以下の形式でお願いします。

1. 総合評価: この方式は成立するか
2. Critical issues: 実装前に設計変更すべき問題
3. Timing model review
4. Linux / STM32責務分離レビュー
5. BLE MIDIおよびRPCの技術リスク
6. Touch bar architecture review
7. Jazz Engine architecture review
8. MU80 MIDI output architecture review
9. Safety/fail-safe review
10. Raspberry Pi + M5Stamp案との比較
11. 推奨する最小実証実験(MVP)
12. 実装順序

不明点を想像で埋めず、UNO Q固有の仕様については必要ならArduino公式資料を確認してください。既存のLoopian_Rustも参照し、どの考え方が流用でき、どこは独自実装すべきかを分けてください。

---
