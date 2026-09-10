# Architecture Review

レビュー対象: `README.md`, `docs/CONCEPT_REVIEW.md`, `docs/DEVELOPMENT_SPEC.md`（2026-09-10 時点の内容）

UNO Q固有の仕様は本レビュー作成にあたり Arduino公式ドキュメント・データシート・公式フォーラムで裏取りした（出典は末尾）。実装はまだ開始していない。

---

## 1. 総合評価

コンセプト自体（「曲を知っている伴奏者」「主旋律は遅延、タッチは現在時刻」という二時間軸モデル）は音楽的にもソフトウェア設計的にも筋が良く、成立し得る。Linux=音楽的判断層 / MCU=決定論的実行・安全層という責務分離も定石通りで正しい。

一方、**「UNO Q一枚構成」という前提は、チップ性能の問題ではなく、Linux↔STM32間のRPC/Bridge基盤がまだ未成熟・未検証であるという点でプロジェクト最大のリスク**である。コミュニティ計測では往復レイテンシが数ms〜90ms超、Arduino自身も物理トランスポート（LPUART1かSPI3か）を2025年11月時点でまだ公式確認していない（Issue #252、未回答）。曲のノリを左右する「今聞こえている音に対する反応速度」がこの未検証リンクに乗るため、設計は「Bridgeは信頼できない遅延・ジッタ源である」という前提で組む必要がある。

結論: UNO Q採用は継続可能だが、(a) Bridgeを同期呼び出しではなく事前スケジュール配信に限定して使う、(b) M1/M2で実機ベンチマークを取り、(c) 既存のRaspberry Pi + M5Stamp資産（本プロジェクトで既に動いている実装）をフォールバックとして温存する、という3点をセットで進めるべき。

---

## 2. Critical issues（実装前に決めるべき問題）

1. **Bridgeのレイテンシ/ジッタが未保証**。コミュニティ計測ではペイロード無しで往復 3.7–5.3 ms（460800bps高速化時)、小ペイロードで 7–9 ms、787文字相当のペイロードで平均91ms。デフォルトは115200bps。Arduino公式のリアルタイム性保証は現時点で存在しない。→ **1音符ごとに同期RPCで送るような設計は不可**。DEVELOPMENT_SPECの「timestamp付きイベントを事前スケジュール」という方針は正しいが、これを徹底する必要がある。
2. **クロック同期方式が未定義**。Linux monotonic clockとSTM32ローカルクロックの対応付け(`SYNC / TIMEBASE`メッセージ)が、上記のジッタを踏まえると単純な1往復同期では不十分。NTP的なオフセット+ドリフト推定、周期的再同期、ドリフト補正が必要。
3. **タッチイベントも同じBridgeを通る**。「タッチは即時」という設計意図に反し、MCU→Linux方向にも同程度のレイテンシ/ジッタが乗る。「IMMEDIATE」量子化モードは実測するまで前提にしてはならない。
4. **GPIOロジックレベルが3.3V固定**（従来Unoの5V許容とは異なる）。既存の半自動アコーディオンのソレノイドドライバ／MCP23017周りが5V前提で設計されていないか要確認。
5. **MU80への物理MIDI出力パス（USB経由）が未検証**。UNO QでのUSBホストモード/ALSA USB-MIDIクラスドライバの動作実績は公式資料上で確認できなかった。Option A（Linux直送）を選ぶ前に実機smoke testが必要。
6. **安全機構がBridgeの不具合から独立している保証がない**。コミュニティ報告に「`Serial.available()`呼び出しがMCU側を4.5〜8ms止める」という事例がある。ソレノイドの決定論的スケジューラ/watchdogループが、Bridge I/O処理と同じスレッド・同じ優先度で動いていないか確認が必須。

---

## 3. Timing model review

- 固定ms look-ahead（0/300/500/1000ms比較）は妥当な出発点。beat相対ではなくmsで始める判断も正しい。
- ただし「見なし用のlook-ahead（音楽的先読み）」と「Bridge配信の必要マージン」を分けて設計すること。ペイロードが大きいと片道90ms近いジッタが観測されているため、**「execute_us の何ms前までにMCUへ送るか」という最小配信マージン**を明示的にパラメータ化すべき（例: 最低50–100ms前）。
- この観点で **`LOOKAHEAD_MS = 0` はJazz Engineの音楽性A/Bテスト（ホスト内シミュレーション）専用と割り切るべきで、実機でのBridge経由スケジューリングとは両立しない**（配信マージンが取れないため）。DEVELOPMENT_SPECのM2受け入れ基準に、この前提を明記した方がよい。
- Linux側はmonotonic clock (`CLOCK_MONOTONIC`)を使う方針で正しい。STM32側クロックとの共通タイムベースを周期的な同期メッセージで維持し、演奏時間（数分間）でのドリフト蓄積を評価すること。
- タッチの量子化は、Bridgeレイテンシが実測されるまでMVPのデフォルトを `NEXT_16TH`/`NEXT_8TH` にする（spec通り）。`IMMEDIATE`は実測後に条件付きで有効化。

---

## 4. Linux / STM32 責務分離レビュー

構造自体は妥当（変更不要）。2点の明確化を推奨。

1. **音楽的判断は必ずLinux側で「確定イベント（note + timestamp）」にまで還元してからBridgeを渡す**。MCU側に音楽的判断（リトリガー処理等）を後から足したくなる誘惑（レイテンシ改善目的等）に対しては、責務分離を崩すため明確に禁止事項として書いておく。
2. **STM32の安全機能（max-on-time / watchdog / panic-all-off）は、Bridge/Routerプロセスが完全にハングしても機能する**ことをコード上保証する（自分のクロック・割り込みだけで動く独立タイマー）。Critical issue #6と対応。

---

## 5. BLE MIDIおよびRPCの技術リスク

### BLE MIDI (Linux側)
- UNO QのオンボードBluetooth（WCBN3536A、BT5.1）はMPU/Linux側に接続されており、**BLE制御はLinux(BlueZ)側が担当**することをArduino公式フォーラムで確認した。README/CLAUDE.mdの前提（Linux側でBLE受信）は正しい。
- BLE-MIDI（GATT MIDI profile）はBlueZ標準ビルドでは無効になっていることが多く、`--enable-midi`付きで再ビルドするか、BlueZのD-Bus GATT APIを使った自前実装が必要になる可能性が高い。UNO Q付属DebianイメージのBlueZが標準でMIDI対応かは要確認（未確認事項として残す）。

### RPC / Bridge
- 実体は「Arduino Router」というLinux側常駐サービス＋MessagePack-RPC、スター型トポロジ（複数Linuxプロセスが同時にMCUと通信可能）。
- 物理トランスポートはコミュニティでは内部シリアルリンク（LPUART1候補）とされているが、SPI3も候補に挙がっており、**Arduino自身が2025年11月時点のIssueで未確定**と回答保留。ボード付属ソフトウェアスタック自体がまだ発展途上。
- 実測値（コミュニティベンチマーク、公式保証ではない）：
  - ペイロード無し往復: 約3.7–5.3ms（460800bps使用時）
  - 小ペイロード（温度センサ相当）: 約7.3–8.9ms
  - 787文字相当ペイロード: 平均約91ms
  - 推奨呼び出し間隔: 約7.8ms以上（≒127Hz上限）
- 結論: **M1/M2で実機ベンチマーク（一方向/往復、両方向）を自前で取得するまで、Bridgeの性能を設計の前提にしない**。

---

## 6. Touch bar architecture review

- 24〜32区画 + 導電性フィラメント + MPR121（複数個）というMVP方針は妥当で低リスク。
- 隣接パッドの重心/中点補間によるスライド連続性の確保は、静電容量スライダーの一般的な実装として妥当。
- I2Cバス設計を明示すること: 既存MCP23017ベースのソレノイドドライバと、新規追加のMPR121（1〜2個）が同一I2Cバスに乗ると、アドレス設計とノイズ分離の両面で問題になりやすい。UNO Qには通常のUno互換ヘッダのI2C（Wire）とは別に、**Qwiicコネクタ専用の3.3V I2Cバス（Wire1）が独立して存在する**ため、タッチセンサ系をQwiic/Wire1側に分離し、ソレノイド駆動系をWire側に残す構成を推奨。
- MPR121の内部スキャンレート（設定依存、一般に100〜200Hz程度）がfast slide検出の分解能要件を満たすか、フィルタ/感度設定を含めて実機検証が必要。

---

## 7. Jazz Engine architecture review

- position→provisional pitch→nearest-note snapping→direction preservation→jazz_degreeによる語彙拡張→humanizationという多層構造は妥当。Loopianのnote_translation思想の参照範囲としても適切（全体移植ではなく部分参照）。
- Engineを `f(JazzState, event) -> AccompanimentEvent[]` という純粋関数に近い形に保ち、隠れた可変状態を持たせないこと（CLAUDE.mdのdeterminism要件と整合）。
- `recent_melody` / `future_melody` のウィンドウサイズ（何拍分、または何msか）が現状未定義。`future_melody`は`LOOKAHEAD_MS`と自然に対応させ、`recent_melody`は拍数ベースで定義することを推奨。
- ルールベース・LLM不使用のMVP方針は、レイテンシ・説明可能性・テスト容易性の観点で正しい判断。

---

## 8. MU80 MIDI output architecture review

- Option A（Linux直送）/ Option B（STM32が時刻指定でDIN送出）の両論併記＋ベンチマーク推奨は妥当。
- 補足: Option Bは「Bridgeのジッタを消す」わけではない（Linux→MCUの配信自体は同じBridgeを通る）。効果があるのは**最終区間（MCU→DIN出力）のタイミングを、ソレノイド駆動と同じMCUローカルタイマー/スケジューラに揃えられる**点。つまりOption Bの主目的は「アコーディオン生音とMU80伴奏を同一タイムベースで同期させる」ことであり、レイテンシ削減ではなく同期精度向上として位置づけるべき。
- Option A採用時は、UNO Q付属DebianイメージでのUSBホストモード・ALSA USB-MIDIクラスドライバ動作を実機で確認すること（公式資料上、本ボードでの実績確認はできなかった）。

---

## 9. Safety/fail-safe review

- MCU側での max-on-time強制、stale event拒否、watchdog/panic all-off、通信断時all-offという基本設計は妥当で業界標準的。
- 追加すべき項目:
  - BLE切断時と同様のall-off/イベントキャンセルルールを、**Router/Bridge切断時・HEARTBEAT途絶時**にも明示的に定義する。
  - **MCU起動直後、Linuxが一度も接続していない初期状態**でも全出力OFFがデフォルトであることを明記する。
  - コミュニティ報告にある「Bridge I/O呼び出しがMCU側を数ms止める」現象を踏まえ、ソレノイドの決定論的実行ループ（スケジュール実行・watchdog）が、Bridge通信処理と**別スレッド/別優先度**で動作し、Bridge側の遅延がMAX_SOLENOID_ON_MSの超過につながらないことをZephyr上の実装として保証する。

---

## 10. Raspberry Pi + M5Stamp案との比較

| 観点 | UNO Q一枚構成 | Raspberry Pi + M5Stamp |
|---|---|---|
| 統合度 | 1ボード・1電源 | 2ボード・独自リンク設計が必要 |
| Linux側の成熟度 | Debian（新しいイメージ、BLE/USB周りの実績情報が少ない） | Raspbian/Ubuntu、BlueZ・ALSA・USB-MIDIの実績が豊富 |
| MCU↔Linux通信 | Arduino公式Bridge（未成熟、レイテンシ未保証、トランスポート未確定） | 自前設計（UART/WiFi等）だが、挙動を完全に制御・実測できる |
| MCU側リアルタイム性 | STM32U585 + Zephyr（Cortex-M33、ハードリアルタイム向き） | ESP32系（FreeRTOS、WiFi/BTスタックの影響でソフトリアルタイム寄り） |
| 既存資産 | なし（新規検証が必要） | **既存M5Stamp実装がアクチュエータ制御の「functional reference」として既に存在**（DEVELOPMENT_SPEC §7） |

UNO Qはボード統合度・STM32のリアルタイム性という点で理論上優れるが、**Bridgeが未検証である以上、その優位性は「実測で裏付けられるまでは仮説」**である。一方Pi + M5Stampは、通信リンクを自分たちで設計・実測できる上、ソレノイド制御について既に動作実績がある。

推奨: UNO Qを第一候補として維持しつつ、M1でBridgeベンチマークを行い、その結果次第でPi + M5Stamp（またはPi + STM32 Nucleo等）へ切り替えられるよう、Linux側の音楽ロジック（Jazz Engine, melody analysis, BLE MIDI受信）をMCU通信層から明確に分離しておくこと（`shared/protocol/`の抽象化が既に提案されている構成で十分対応可能）。

---

## 11. 推奨する最小実証実験（MVP）

DEVELOPMENT_SPECのM0〜M2を基本的に踏襲しつつ、ベンチマークを独立マイルストーンとして明示する。

- **M0 host simulation**: spec通り。ハードウェア非依存でJazz Engineの音楽性を検証。
- **M1 BLE MIDI ingest + Bridge benchmark（統合）**: BLE MIDI受信の安定性検証に加え、**Linux→MCU・MCU→Linux双方向の往復/片道レイテンシとジッタ分布を実機計測**し、UNO Q単体構成の go/no-go をここで判断する。
- **M2 fixed-delay accordion relay**: spec通りだが、Critical issue #2の配信マージン基準（例: 実行時刻の50–100ms以上前に送信）を受け入れ基準に追加する。
- M3以降はspec通りで問題ない。

---

## 12. 実装順序

DEVELOPMENT_SPEC §16のマイルストーン順序を基本的に踏襲。ただし **M1とM2の間に「Bridge実測に基づくgo/no-goゲート」を挿入**し、ここでUNO Q継続かPi+M5Stamp切り替えかを判断してからM2（物理配線・タイムクリティカルな実装）に進むこと。これにより、後戻りが最も高コストになる「ハードウェア配線後」にアーキテクチャ選定ミスが発覚するリスクを避けられる。

---

## 出典（Arduino公式・公式フォーラム等）

- [UNO Q | Arduino Documentation](https://docs.arduino.cc/hardware/uno-q)
- [UNO Q User Manual | Arduino Documentation](https://docs.arduino.cc/tutorials/uno-q/user-manual/)
- [Arduino® UNO Q Datasheet (ABX00162)](https://docs.arduino.cc/resources/datasheets/ABX00162-datasheet.pdf)
- [Clarification on communication transport between STM32U585 and QRB2210 on UNO Q · Issue #252 · arduino/ArduinoCore-zephyr](https://github.com/arduino/ArduinoCore-zephyr/issues/252)
- [GitHub - arduino/arduino-router](https://github.com/arduino/arduino-router)
- [How do I use the arduino Uno Q's Bluetooth capability in my projects? - Arduino Forum](https://forum.arduino.cc/t/how-do-i-use-the-arduino-uno-qs-bluetooth-capability-in-my-projects/1422804)
- [Performance of Interprocessor communication - Arduino Forum](https://forum.arduino.cc/t/performance-of-interprocessor-communication/1413270)
- [Evaluated Uno Q Router / Bridge Latency with MAX31855 - Arduino Forum](https://forum.arduino.cc/t/evaluated-uno-q-router-bridge-latency-with-max31855/1423998)
- [GPIO pins in Arduino UNO Q - Arduino Forum](https://forum.arduino.cc/t/gpio-pins-in-arduino-uno-q/1437113)
- [Arduino UNO Q is now available with 4GB RAM and 32GB storage! | Arduino Blog](https://blog.arduino.cc/2026/01/20/arduino-uno-q-is-now-available-with-4gb-ram-and-32gb-storage/)
- [Arduino UNO Q 4GB board with 4GB RAM, 32GB storage is now available for $59 - CNX Software](https://www.cnx-software.com/2026/01/21/arduino-uno-q-4gb-board-with-4gb-ram-32gb-storage-available-59/)

※フォーラム上のレイテンシ数値はコミュニティ計測であり、Arduino公式のリアルタイム性保証ではない。実装判断の最終根拠は自前の実機計測（M1マイルストーン）とすること。
