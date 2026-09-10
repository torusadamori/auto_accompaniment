# UNO Q 開発基盤

このディレクトリ群は、UNO Qホスト上のGit checkoutを正本として、Python環境、App Lab配置、BLE受信、ログ、診断を一貫して扱う。M3のBLE→LED実験を最初の利用対象にしているが、アドレス、UUID、配置先、配置ファイルは `config/unoq.env` に集約してあり、後続機能でも同じ基盤を使える。

## 覚える操作は3つ

UNO Qターミナルでrepo rootへ移動して実行する。

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

`setup.sh` はvenvを無ければ作成し、requirementsを導入し、ログディレクトリを用意する。再実行時は既存venvを再利用し、requirementsの望ましい状態へ収束する。Python/BlueZ/App CLI/App Lab配置も確認する。

`run.sh` はvenvとBleakを確認し、安全なApp同期を行い、relayが無ければAppを起動して最大設定時間待ち、BLE receiverを開始する。iPhoneアドレスやsocketの長い引数を入力する必要はない。Ctrl+Cで終了する。

`doctor.sh` は各項目を `PASS` / `WARN` / `FAIL` で表示し、1件でも `FAIL` があれば非ゼロで終了する。iPhoneが広告していない、Appがまだ起動していない、Gitに作業中の変更がある、といった一時的な状態は原則 `WARN` になる。

## 初回のApp Lab準備

現在のrepoには、実機で成功済みのApp Lab `app.yaml` と `sketch.yaml` を意図的に収録していない。App Labで一度だけ、公式 `Blink LED from Python` の成功済みAppを複製し、デフォルトでは次へ置く。

```text
/home/arduino/ArduinoApps/m3-ble-to-led
```

このAppに `app.yaml` がある状態で `setup.sh` を再実行する。その後、repo所有の3ファイルは自動配置される。`app.yaml`、`sketch.yaml`、その他のApp Lab管理ファイルは同期対象外であり、変更しない。

## 設定

通常変更する場所は [`config/unoq.env`](../config/unoq.env) だけである。

| 変数 | 用途 | デフォルト |
|---|---|---|
| `UNOQ_VENV_DIR` | ホストPython venv | `~/blemidi` |
| `UNOQ_RECEIVER_MODULE` | 起動するホストreceiver | `m3.receiver` |
| `UNOQ_APP_DIR` | App Labの既存App | `/home/arduino/ArduinoApps/m3-ble-to-led` |
| `UNOQ_RELAY_SOCKET` | ホストから見えるrelay socket | App内 `m3-led.sock` |
| `UNOQ_BLE_ADDRESS` | iPhone/送信機のBlueZ address | 実機検証済み値 |
| `UNOQ_BLE_MIDI_SERVICE_UUID` | BLE MIDI service | 標準UUID |
| `UNOQ_BLE_MIDI_CHARACTERISTIC_UUID` | BLE MIDI characteristic | 標準UUID |
| `UNOQ_WAIT_SECONDS` | App/relay最大待ち時間 | 120秒 |
| `UNOQ_APP_START_MODE` | App起動経路 | `cli` |
| `UNOQ_UPDATE_ON_RUN` | 通常run前のGit更新 | `0` |

別設定を使うときは、信頼できるenvファイルを用意し、`UNOQ_CONFIG_FILE=/path/to/file ./scripts/unoq/run.sh` とする。設定ファイルはshellとして読み込まれるため、出所不明のファイルを指定しない。

## 更新と配置

ネットワーク更新だけを行う:

```bash
./scripts/unoq/update.sh
```

現在commitを表示してからfetchと `pull --ff-only` を行う。tracked/untrackedを含むdirty treeではpullせず、stash、reset、強制checkoutも行わない。

配置だけを確認する:

```bash
./scripts/unoq/deploy.sh --dry-run
```

`deploy.sh` は `UNOQ_DEPLOY_FILES` のみをrepoからAppへ同期する。App側がrepoの現行版またはGit履歴上の旧版なら更新できる。Git履歴にない手編集を検出した場合は、何も書かず停止する。

手編集をrepo版に戻すと決めた場合だけ、まず対象を確認し、明示的に実行する。

```bash
./scripts/unoq/deploy.sh --dry-run --sync-existing
./scripts/unoq/deploy.sh --sync-existing
```

置換前の全ファイルは `<App>/.unoq-backups/<日時>-<ID>/` に保存される。途中のbackupに失敗すれば置換は始めない。単独の `deploy.sh` は稼働中を示すrelay socketがある場合に配置を拒否する。`--stop-running` を明示した場合と通常の `run.sh` だけは、変更が実際に必要なときに限って公式 `arduino-app-cli app stop` を呼び、socketが正常に消えたことを待ってから配置する。socketそのものは削除しない。

## Appの自動起動

Arduino公式の `arduino-app-cli` はUNO Q上でArduino AppのLinux/MCU両方を管理・起動するツールであり、`app start app_path` が正式に実装されている。そのためデフォルトは次相当を使用する。

```bash
arduino-app-cli app start "$UNOQ_APP_DIR"
```

実機イメージが古くCLIを利用できない場合は、推測したDocker/書込コマンドへ迂回しない。公式環境を更新するか、`UNOQ_APP_START_MODE="gui"` に設定する。この場合だけ `run.sh` の案内後にApp Labで対象Appの **Run** を1回押す。スクリプトはrelayを待って自動的に続行する。

## runオプションとログ

```bash
./scripts/unoq/run.sh --update         # clean treeを確認して更新後に起動
./scripts/unoq/run.sh --dry-run        # 更新/配置/起動/BLEを変更なしで確認
./scripts/unoq/run.sh --raw            # BLE packetの16進ログを追加
./scripts/unoq/run.sh --sync-existing  # backup付きでApp手編集をrepo版へ戻す
```

各実行ログは `logs/unoq/<UTC日時>-<PID>.log` に保存される。`logs/unoq/latest.log` は実行開始時に今回のログを指すsymlinkへ原子的に更新される。最後に終了コード、所要時間、ログパスを含む短い `PASS` / `FAIL` を表示する。

`run.sh` の通常実行はネットワーク更新をしない。必要なときだけ `--update` を使うため、GitHubやネットワークの一時障害で演奏開始を妨げない。

## doctorの診断内容

一括診断する項目は次のとおり。

- Python 3.11以上、設定venv、Bleak（検証版は3.0.2）
- BlueZ (`bluetoothctl`)、Bluetooth adapterの存在と電源
- 設定したBLE deviceの検出
- BLE MIDI service UUIDとnotify characteristic UUIDの実機GATT確認
- App Lab directoryと `app.yaml`
- 公式Arduino App CLI
- relay socketの種類
- relayから同じLED状態を再送する `PROBE` による実Bridge RPC往復
- Git branch/upstream/dirty状態

BLEのGATT診断時はiPhone MIDIアプリを送信可能・広告可能な状態にする。別receiverが接続中なら競合を避けて先に停止する。`PROBE` は現在の論理LED状態と同じ値をBridgeへ送り、状態を変えずにLinux container→Router Bridge→STM32のACKを確認する。

## 安全上の境界

- repoが正本。App Lab側の未知の変更は自動mergeも自動破棄もしない。
- App全体のコピー、削除、`rm -rf`、Git reset/stash、dirty treeへのpullは行わない。
- socketが通常ファイルやsymlinkなら削除せず `FAIL` にする。
- 自動停止は配置差分がある通常runに限定し、公式App CLIを使用する。差分がなければ稼働Appを止めない。
- App Lab/MCUの起動は公式CLIだけを使う。非公式Docker構成や書込手順は推測しない。
- `setup.sh` は既存の非venvディレクトリをvenvとして上書きしない。
- M3の旧 `scripts/m3_run_unoq.sh` と `m3.launcher` は互換用に残す。新規開発と通常運用は `scripts/unoq/` を使う。

## 開発PCでの検証

ハードウェア非依存テストと構文検査:

```bash
python -m unittest discover -s tests -v
python -m compileall -q m3 unoq experiments/m3_app/python tests
bash -n scripts/unoq/*.sh scripts/m3_run_unoq.sh
```

BLE scan、App start、MCU flash、Bridge `PROBE` はUNO Q実機で `doctor.sh` と `run.sh` を使って確認する。開発PCで実機成功を推測しない。

## 公式資料

- [Arduino App CLI](https://github.com/arduino/arduino-app-cli) — UNO Q上でArduino Appを管理・実行する公式CLI
- [Arduino App CLI `app start`](https://github.com/arduino/arduino-app-cli/blob/main/cmd/arduino-app-cli/app/start.go) — `Use: "start app_path"` の実装
- [Arduino App specification](https://github.com/arduino/arduino-app-cli/blob/main/docs/app-specification.md)
