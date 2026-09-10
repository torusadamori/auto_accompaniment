# UNO Q 開発基盤

Git checkoutを正本にし、UNO Q上のPython環境、App Lab配置、Linux MIDI入力、relay、ログを一つの操作系で扱う。

## 3つの操作

UNO Qのrepo rootで実行する。

初回:

```bash
./scripts/unoq/setup.sh
```

通常:

```bash
./scripts/unoq/run.sh
```

異常時:

```bash
./scripts/unoq/doctor.sh
```

`setup.sh` はvenvを安全に作成・再利用し、`python-rtmidi` を導入し、ログディレクトリ、BlueZ、PipeWire/ALSA、App CLI、App配置を確認する。`run.sh` はAppファイルを同期し、Appを公式CLIで起動してrelayを待ち、Linux MIDI入力を自動選択して受信する。iPhoneのMAC、scan、長い引数は不要である。

## 正しいBLE MIDI経路

```text
iPhone MIDI Wrench (Central)
        ↓ BLE MIDI
UNO Q / toru1 (Peripheral)
        ↓ BlueZ + WirePlumber/BlueZ-MIDI または BlueALSA
Linux ALSA/JACK MIDI input
        ↓ m3.receiver / ActiveNotes
App Lab relay socket → Bridge → MCU
```

`m3.receiver` はBLE GATT clientではない。`BleakClient` でiPhoneへ接続せず、UNO QのBLE-MIDIサービスがLinuxへ公開したネイティブMIDI入力をRtMidiで受ける。iPhoneで `toru1 Connected / Input / Output` なら、`run.sh` は対応ポートをそのまま開く。ポートが未出現または切断された間はLED OFFで待ち、再出現時に自動復帰する。

WirePlumberでは `monitor.bluez-midi` がBlueZ MIDIを管理し、`bluez_midi.server` がBLE-MIDI serviceを提供できる。実機がWirePlumber経路かBlueALSA経路かは決め打ちせず、`doctor.sh` がPipeWire graph、ALSA/JACK入力、BlueZ接続情報を表示する。

## App Labの初回準備

repoは実機固有の `app.yaml` と `sketch.yaml` を所有しない。App Labで一度だけ公式 `Blink LED from Python` の成功済みAppを複製し、既定では次へ置く。

```text
/home/arduino/ArduinoApps/m3-ble-to-led
```

`app.yaml` がある状態で `setup.sh` を実行する。repoが同期するのは `config/unoq.env` の `UNOQ_DEPLOY_FILES` だけで、App Lab管理ファイルは変更しない。

## 設定

設定は [`config/unoq.env`](../config/unoq.env) に集約する。

| 変数 | 用途 | 既定値 |
|---|---|---|
| `UNOQ_VENV_DIR` | ホストPython venv | `~/blemidi` |
| `UNOQ_APP_DIR` | 既存App Lab App | `/home/arduino/ArduinoApps/m3-ble-to-led` |
| `UNOQ_RELAY_SOCKET` | relay Unix socket | App内 `m3-led.sock` |
| `UNOQ_MIDI_PORT_PATTERN` | ALSA/JACK入力の自動選択 | BlueZ/BLE MIDI/`toru1` |
| `UNOQ_MIDI_RETRY_SECONDS` | ポート再検出間隔 | 2秒 |
| `UNOQ_MIDI_PROBE_SECONDS` | doctorの受信probe時間 | 1秒 |
| `UNOQ_WAIT_SECONDS` | App/relay最大待ち | 120秒 |
| `UNOQ_APP_START_MODE` | App起動経路 | `cli` |

固定iPhone addressやremote GATT UUIDは設定しない。別設定は、信頼できるenvを `UNOQ_CONFIG_FILE=/path/to/file` で指定する。

## 更新・配置・ログ

```bash
./scripts/unoq/update.sh
./scripts/unoq/deploy.sh --dry-run
./scripts/unoq/run.sh --update
./scripts/unoq/run.sh --raw
```

`update.sh` はdirty treeならfetch/pullせず、stash/resetもしない。`deploy.sh` はrepo履歴にないApp手編集を保護する。明示的な `--sync-existing` のときだけ `.unoq-backups/` へ保存後に置換する。

App停止後に `m3-led.sock` が残った場合、スクリプトは次をすべて満たすときだけ、その1個をstaleとして削除する。

- 対象がAppディレクトリ直下のUnix socket
- relayへの `PING` が失敗
- 公式 `arduino-app-cli app list` がApp停止を示す、または直前の公式 `app stop` が成功

通常ファイル、symlink、稼働中または状態不明のsocketは削除しない。

ログは `logs/unoq/<UTC日時>-<PID>.log` に保存し、`logs/unoq/latest.log` が最新を指す。終了時に `PASS` / `FAIL`、時間、ログパスを表示する。

## App起動

既定はArduino公式 `arduino-app-cli app start app_path` を使う。CLIが利用できない実機だけ `UNOQ_APP_START_MODE="gui"` とし、表示後にApp Labで **Run** を1回押す。非公式なDocker操作や起動方法は推測しない。

## doctorの診断

`doctor.sh` は次を `PASS` / `WARN` / `FAIL` で表示する。

- Python、venv、python-rtmidi
- BlueZ、adapter電源、取得できる場合はiPhoneを含む接続済みpeer
- PipeWire上のBlueZ/BLE MIDI provider、ALSA/JACK MIDI入力一覧と選択結果
- 選択ポートを並列openし、送信やLED変更を行わない短時間の実MIDI probe
- App Lab directory、公式App CLIのApp稼働状態
- relay socket種別、状態を変更しないBridge `PROBE`
- Git branch/upstream/dirty状態

probe中に演奏すれば受信byteも表示する。イベントがなくてもポートの並列open成功は確認でき、既存receiverを停止したりMIDIを送信したりしない。

## テスト

```bash
python -m pytest -q
python -m compileall -q m3 unoq experiments/m3_app/python tests
bash -n scripts/unoq/*.sh scripts/m3_run_unoq.sh
```

開発PCではハードウェア状態を推測しない。実機経路はUNO Q上の `doctor.sh` が採取する。

## 公式資料

- [WirePlumber Bluetooth MIDI configuration](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html)
- [PipeWire MIDI design](https://docs.pipewire.org/page_midi.html)
- [Arduino App CLI](https://github.com/arduino/arduino-app-cli)
- [Arduino App CLI `app start`](https://github.com/arduino/arduino-app-cli/blob/main/cmd/arduino-app-cli/app/start.go)
- [Arduino App CLI `app list`](https://github.com/arduino/arduino-app-cli/blob/main/cmd/arduino-app-cli/app/list.go)
