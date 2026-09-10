# M3 BLE MIDI → MCU LED

M3は、iPhoneのMIDI Wrenchから届くNote On/OffをUNO Qの内蔵LEDへ伝える最小統合試験である。運用手順は [UNO Q開発基盤](UNOQ_DEVELOPMENT.md) を使用する。

## 接続方向

```text
iPhone MIDI Wrench (BLE Central)
  → toru1 / UNO Q (BLE MIDI Peripheral)
  → BlueZ系サービスが公開するLinux MIDI input
  → m3.receiver
  → /home/arduino/ArduinoApps/m3-ble-to-led/m3-led.sock
  → App Lab Linux container
  → Router Bridge
  → STM32 LED
```

過去の `UNO Q → BleakClient → iPhone Peripheral` という仮定は実機状態と逆だったため廃止した。iPhoneのMAC address、BLE scan、remote serviceへのGATT接続は使わない。

## 実行

初回:

```bash
./scripts/unoq/setup.sh
```

通常:

```bash
./scripts/unoq/run.sh
```

iPhone側で `toru1 Connected / Input / Output` ならそのままNoteを送る。`run.sh` はLinux MIDI inputを自動検出する。入力が切断されるとactive noteを消去してLED OFFを要求し、再接続を待つ。

異常時:

```bash
./scripts/unoq/doctor.sh
```

診断はBlueZ接続、PipeWire/ALSA/JACK MIDI経路、短時間の非破壊受信probe、App状態、relay socketとBridge応答をまとめて表示する。ログは `logs/unoq/latest.log` を参照する。

## 安全動作

- 初期接続、MIDI入力断、receiver終了、relay client断、heartbeat timeoutでLED OFFを要求する。
- relayは1クライアントだけを出力ownerにし、doctorの `PROBE` は所有権を奪わない。
- App Labの未知の手編集は上書きせず、明示同期時にもbackupする。
- stale socketは、App停止とlistener不在を確認した正確な `m3-led.sock` だけ削除する。

BLE MIDI framingはBlueZ/WirePlumberまたはBlueALSA側でnative MIDIへ変換される。`m3.receiver` は変換済みのMIDI 1.0 messageを既存 `ActiveNotes` に渡す。
