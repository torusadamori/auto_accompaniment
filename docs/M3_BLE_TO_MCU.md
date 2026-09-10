# M3: BLE MIDI → Linux/Python → Bridge → STM32 → LED

このM3は実験ログ §18 の「BLE→LED統合」を指す。DEVELOPMENT_SPEC §16 の将来のM3（Touch bar）とは別。固定look-ahead、Jazz Engine、ソレノイド、MU80出力は実装しない。

## 構成

```text
iPhone（検証済みMIDIアプリ / BLE MIDI GATT server）
  │ characteristic notification
  ▼
UNO Q ホストLinux: BlueZ → bleak 3.0.2
  m3.receiver → BLE MIDI parser → active set {(channel, note)}
  │ Unix domain socket: <Appのホスト側ディレクトリ>/m3-led.sock
  │ 同じファイルをコンテナから /app/m3-led.sock として参照
  ▼
App Labコンテナ: python/main.py + led_relay.py
  │ Bridge.call("set_led_state", bool)
  ▼
Arduino Router → Arduino_RouterBridge → STM32U585
  set_led_state(bool) → LED_BUILTIN（LOW = 点灯）
```

BLEは成功済みのホストvenvで受信する。ArduinoライブラリはApp Labに付属するものを使い、ホストvenvへ追加しない。App LabのBridge初期化・起動管理は公式例と同じ `App.run(user_loop=...)` に任せる。

公式App CLIのCompose生成コードはアプリディレクトリを `/app` へbind mountする。この共有パスでUnix socketを使い、コンテナのネットワークモードには依存しない。実機のApp Lab 0.10.0でも下記のmount検査を行うこと。最新版公式ソースの確認と、当該バージョンの実測は区別する。

## ファイル

| ファイル | 役割 |
|---|---|
| `scripts/m3_run_unoq.sh`, `m3/launcher.py` | ワンコマンド起動・保護付きApp同期 |
| `m3/midi.py` | ハードウェア非依存パーサ、active note set |
| `m3/receiver.py` | ホストBLE購読、ログ、状態変更・heartbeat送信 |
| `m3/requirements.txt` | ホスト用Bleak（実験済みバージョン） |
| `experiments/m3_app/python/main.py` | App Lab起動エントリ |
| `experiments/m3_app/python/led_relay.py` | ローカルsocket → LED専用RPC |
| `experiments/m3_app/sketch/sketch.ino` | 起動時OFF、boolのLED APIのみ |
| `tests/test_midi.py`, `tests/test_transport.py` | パーサ・状態・通信テスト |

このリポジトリには以前の実機Bridgeコードは収録されていなかった。実機の成功済みプロジェクトは保存し、必ずコピーを編集する。`app.yaml` / `sketch.yaml` のバージョン・依存ライブラリ設定もその成功済みコピーから引き継ぐ。`experiments/m3_app` 単体を完成したApp Labインポート用パッケージとして扱わない。

## ワンコマンド起動（推奨）

既存の `~/auto_accompaniment`、`~/blemidi`、M3 Appのコピーを利用する。初回はrepo rootで `git pull --ff-only` してこのスクリプトを取得する。以降はこれだけでよい：

```bash
./scripts/m3_run_unoq.sh
```

スクリプト自身の位置からrepo rootを特定するため、別ディレクトリから絶対パスで呼んでも動作する。venvのactivate、ファイルの手動コピー、アドレス/socketを含む長いコマンド入力は不要。

実行順序：

1. `~/blemidi/bin/python` と、既存Appの `app.yaml` を確認する。
2. Appの `python/main.py`、`python/led_relay.py`、`sketch/sketch.ino` をrepoと比較する。
3. 必要なファイルだけを同期する。既存ファイルを置換する場合は、まずApp内の `.m3-backups/<日時>-<識別子>/` に全原本のバックアップを作成する。
4. `bleak==3.0.2` を確認する（自動pip installはしない）。
5. `/home/arduino/ArduinoApps/m3-ble-to-led/m3-led.sock` を確認する。まだ無ければ **「App LabでM3 BLE to LEDをRunしてください」** と表示して最大120秒待つ。Runすると同じコマンドが続行する。タイムアウトなら理由付きで終了し、Run後に同じコマンドを再実行する。
6. 同じvenv Pythonで `m3.receiver` を起動し、`9C:C3:94:81:01:53` からのnotifyを待つ。終了はCtrl+C。

この旧M3 launcherを作成した時点では、App Lab 0.10.0実機のCLI起動経路を確認できていなかったため、GUIのRun操作を残している。その後、Arduino公式 `arduino-app-cli` の安定版と `app start app_path` / `app stop app_path` の実装を確認し、新しい [`scripts/unoq/` 基盤](UNOQ_DEVELOPMENT.md) では公式CLIによる起動・必要時停止を使用する。通常運用は新基盤を使い、この旧launcherは互換用とする。非公式Docker操作やMCU書込手順は引き続き推測実装しない。

### ファイル保護

- 内容が一致するファイルは書き換えない。通常のreceiverだけの更新ではAppの再起動は不要。
- 異なる既存ファイルは、repoの過去のM3版と完全一致するものだけ自動更新する。App Lab上の独自編集・貼り付け差分・履歴のない版は保護して停止する。
- 独自編集をrepo版に置き換える場合は、まず `./scripts/m3_run_unoq.sh --dry-run --sync-existing` で対象を確認し、必要なら `./scripts/m3_run_unoq.sh --sync-existing` でバックアップ付き同期する。自動mergeはしない。
- 同期が必要なのにsocketが残っている場合は、稼働中かどうかを推測せず停止する。App LabでM3をStopしてから同じコマンドを実行し、同期後の案内に従ってRunする。この更新時だけStop/Runが必要。
- `app.yaml`、`sketch.yaml`、その他のAppファイルは変更しない。Appや対象ファイルがsymlinkの場合は拒否する。成功済みの別Appを上書きしないよう、表示されるAppパスを確認する。
- socketの存在・種類・権限を確認するが、確認目的の接続/LED操作はしない。実際の接続・最初のOFF ACKはreceiverで行う。古いsocketが残って接続拒否になる場合は既存の復旧手順を使う。socketを自動削除しない。

### オプション

```bash
./scripts/m3_run_unoq.sh --dry-run       # 読み取りのみ。コピー・待機・BLE接続なし
./scripts/m3_run_unoq.sh --pull          # clean treeでgit pull --ff-only後に実行
./scripts/m3_run_unoq.sh --raw           # 生BLEパケットも表示
./scripts/m3_run_unoq.sh --wait-seconds 0 # リレー未起動時は待たずに案内して終了
```

通常起動ではネットワークに依存するgit更新を行わない。`--pull` は未commit/未追跡変更があると停止し、stash/reset/強制更新は行わない。pull失敗時も受信を開始しない。更新後は新しいlauncherを読み直す。`--dry-run --pull` は実際にはpullせず、現在のcheckoutだけを確認する。

環境を変更したときだけ以下を使う（通常は設定不要）：

| 設定 | 方法 | デフォルト |
|---|---|---|
| venv | 環境変数 `M3_VENV` | `~/blemidi` |
| App dir | `--app-dir` または `M3_APP_DIR` | `/home/arduino/ArduinoApps/m3-ble-to-led` |
| iPhone | `--address` または `M3_BLE_ADDRESS` | `9C:C3:94:81:01:53` |
| socket | `--socket` | 指定App dir内の `m3-led.sock` |

バックアップから戻す場合はM3をStopし、表示されたバックアップディレクトリの必要なファイルを対応するAppの場所へ戻す。再起動はApp LabのRunを使う。

以下は初回セットアップや問題切り分け時の手動手順として残す。

## 起動手順

### 1. ホストLinuxにリポジトリを用意

以下はWindowsではなく、UNO QへSSH等で入ったホストLinuxで実行する。既存cloneがあればそのディレクトリで `git pull --ff-only` する。

```bash
git clone https://github.com/torusadamori/auto_accompaniment.git
cd auto_accompaniment
source ~/blemidi/bin/activate
python -m pip install -r m3/requirements.txt
python -m unittest discover -s tests -v
```

既存の `~/blemidi` venvを利用する。Python 3.11以上が必要（実機記録は3.13）。以下でもリポジトリのルートをカレントディレクトリとする。

### 2. 成功済みAppをコピー

1. App Labで `Blink LED from Python` の成功済みプロジェクトを複製し、`M3 BLE to LED` と命名する。
2. コピーの `python/main.py` を本リポジトリの同名ファイルに置換する。
3. コピーの `python/led_relay.py` を追加する。
4. コピーの `sketch/sketch.ino` を本リポジトリの同名ファイルに置換する。
5. 他のBridge実験Appを停止し、このコピーをRunする。MCUのビルド・書き込みもApp Labに任せる。
6. Appログに `LED OFF (Bridge acknowledged)` と `M3 relay listening on /app/m3-led.sock` が出ることを確認する。

LEDは起動時OFF。Bridge関数がまだ登録されていない等で初期OFFが失敗したら、MCUの書き込み完了とスケッチを確認してAppを再実行する。

### 3. ホストから共有socketを確認

起動中のPythonコンテナ名を確認し、`APP_CONTAINER` に実際の名前を設定する。Docker操作に権限が必要なら下記の `docker` のみ `sudo docker` に読み替える。

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}'
APP_CONTAINER='実際のM3のPythonコンテナ名'
docker inspect "$APP_CONTAINER" --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
APP_DIR=$(docker inspect "$APP_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/app"}}{{.Source}}{{end}}{{end}}')
test -n "$APP_DIR" && test -S "$APP_DIR/m3-led.sock"
ls -l "$APP_DIR/m3-led.sock"
id -u
docker exec "$APP_CONTAINER" id -u
```

`/app` へのbind mountとsocketが存在し、ホストvenv実行ユーザーとコンテナ実行ユーザーのUIDが一致することを確認する。socketは0600で作成する。UIDが異なる環境では所有ユーザーのホストセッションで受信を実行する。Bluetoothのために受信スクリプト全体をsudo実行したり、socketを全員書き込み可にはしない。

BLEより先にLED経路だけ確認できる（受信プロセスは停止しておく）：

```bash
python - "$APP_DIR/m3-led.sock" <<'PY'
import socket, sys, time
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(3)
    s.connect(sys.argv[1])
    with s.makefile('rb') as reply:
        for state in (b'0\n', b'1\n', b'0\n'):
            s.sendall(state)
            assert reply.readline() == b'OK\n'
            time.sleep(1)
print('OFF -> ON -> OFF acknowledged')
PY
```

## iPhone接続とBLE受信

1. 以前Python/Bleakで成功したiPhone MIDIアプリと接続設定を再利用する。アプリを前面にし、BLE MIDIの公開・送信を有効にする。今回のクライアントは**iPhone側がMIDI Service/Characteristicを公開する構成**である。
2. 初回のbondが必要なら、実験ログで成功した `bluetoothctl` のpair/connect手順を済ませる。UNO QからUUIDを広告するだけではMIDI GATT serverにはならず、本実装もUNO Q側serverは提供しない。
3. 既存 `ble_midi_monitor.py` を停止する。`bluetoothctl` のGATT通知を有効にしていた場合は同じcharacteristicで `notify off` にし、監視を終了する。bondは削除しない。
4. 既知のiPhoneアドレスを `bluetoothctl devices` / `bluetoothctl info <address>` で確認する。広告している端末の補助検索には `python -m m3.receiver --scan` も使える。接続済みiPhoneが広告一覧に出ない場合は既知アドレスを使う。
5. ホストvenvから実行する。アドレスはプレースホルダーを実値に置換する。

```bash
python -m m3.receiver --address AA:BB:CC:DD:EE:FF \
  --socket "$APP_DIR/m3-led.sock" --raw
```

Service `03b80e5a-ede8-4b33-a751-6ce34ec4c700`、Characteristic `7772e5db-3868-4112-a1a9-f2669d106bf3` を確認し、`read_gatt_char()` を挟まず直接 `start_notify()` で購読する。`BLE MIDI subscribed` が出たらiPhoneから鍵盤またはTulipを送信する。通常は `--raw` を外してよい。

ログ例（channelは1〜16）：

```text
NOTE ON ch=1 note=60 vel=80 active=1
NOTE ON ch=1 note=64 vel=80 active=2
NOTE OFF ch=1 note=60 vel=0 active=1
NOTE OFF ch=1 note=64 vel=0 active=0
```

`rx=` はホストmonotonic秒。送信側タイムスタンプはフレーミングとして読み飛ばすため、この値からiPhone→LEDの遅延を算出することはできない。

## 実機の合格条件

- 単音：Note Onで点灯し、対応Note Offで消灯する。
- 和音：CとEを押し、Cだけ離しても点灯を維持し、Eも離すと消灯する。
- 複数channel：別channelの同じnoteを押し、一方を離しても他方が残れば点灯する。
- velocity 0のNote On：Note Offとして扱われる。
- Tulip全曲：CC / Program Change / SysEx / Realtimeを挟んでもログが継続し、最後にactive=0・消灯する。CC123は該当channelだけクリアする。
- 鍵盤を押したままBLE切断、受信側Ctrl+C：OFFとなる。再実行時は空のactive setから始まる。
- 受信プロセスを強制終了：リレーがEOFまたは最後のheartbeatから約3秒でOFFにする（Bridge健全時）。
- App Lab停止後の復旧：AppをRunし、初期OFFを確認してから受信スクリプトを再実行する。

短いNote On/Offの連続は目で見えない場合がある。最初は各音を1秒程度保持する。点灯しないときは上記socket単体テスト、Appログ、BLEログの順に区間を切り分ける。

## 通信・パーサの仕様と制約

- 通信は `0\n` / `1\n`、応答は `OK\n`。状態変更時は `Bridge.call()` 完了後に応答する。同じ状態のheartbeatはRPCを再実行せず、リレーの接続期限だけ更新する。
- 受信接続は1本だけ。2本目は既存のLED状態を変えず拒否する。ホストは約1秒ごとにheartbeatを送り、リレーは約3秒の無応答、EOF、不正コマンドでOFFを要求する。
- BLE通知キューは256パケット。overflow、1秒超の滞留、不正パケット、通信エラー時は終了してOFFを要求する。自動再接続はしない。ログを確認して再起動する。
- 複数イベント、timestampあり/なしRunning Status、全channel voice長、System Common、Realtime、パケットをまたぐSysExの読み飛ばしに対応。Running StatusはBLEパケット内だけ保持する。SysEx payloadは保存しないため長さに比例したメモリ増加はない。
- BLE仕様に従い、System Common/Realtimeを挟んでも同一パケットのRunning Statusを保持する。その直後のRunning Statusにはtimestampが必要。通常のMIDIバイト列とは区別する。
- Realtimeは独立イベントまたはSysEx中のtimestamp付き挿入に対応。Noteメッセージのデータ途中への生Realtime挿入はBLE MIDI仕様の対象外であり、不正パケットとして停止する。
- active setは `(channel, note)` 単位。同じキーへの重複Note Onは1音とし、1回のOffで解除する。音源のvoice数を数えるものではない。未対応Offは無視する。
- CC120/123とAll Notes Offを含むchannel mode（124〜127）は該当channelを解除、System Resetは全channelを解除する。CC121、通常CC、Program ChangeはLEDに影響しない。サステイン(CC64)は反映せず、鍵盤の押下状態を表示する。
- このLED実験にMCU watchdogはない。App/Routerの強制停止・ハング、Bridge障害ではOFFを保証しない。MCU再起動中はリレーのキャッシュとLED状態がずれることがあるため、両プロセスを再起動する。ソレノイドには流用しない。
- `Bridge.call()` の待ち時間は実機付属ライブラリのデフォルトに従う。約3秒の期限はMCUレベルの保証ではなく、Bridge待ち中は期限処理も遅れる。
- 正常終了時にsocketファイルを削除する。強制停止後に `Address already in use` となった場合は、対象Appと受信プロセスを停止したことを確認してから **そのコピーの** `$APP_DIR/m3-led.sock` だけを削除してRunする。稼働中socketを自動削除しない。
- Unix socketのパス長にはOSの上限がある。長いApp名や深い配置でbind/connectに失敗する場合はコピーを短い名前・パスにする。

## 検証結果（開発PC、2026-09-10）

開発PCで50テストを実行し、49件成功・Unix socketの1件skip。compileallとBash構文チェックも成功。launcherの同期/バックアップ/上書き拒否/dry-run/git更新/receiver引数を含む。UNO Q上のスクリプト実行は未検証。

WindowsのPythonでパーサ・active set・TCPフレーム・EOF・期限切れ・RPC失敗・疑似Bridgeへの統合経路をテストする。Unix socketを提供しないPythonではその1件を明示的skipする。UNO QのLinux Pythonでは同じテストがUnix socketの接続・ACK・ファイル削除も検証する。

```bash
python -m unittest discover -s tests -v
python -m compileall -q m3 experiments/m3_app/python tests
```

実機のBLE購読とMCUビルド・書き込み・LED点灯はこの開発PCから実行していない。上の合格条件の実測結果を後続の実験ログへ記録すること。

## 購読前終了の修正と再テスト

実機から、socket→LEDは成功し、単純なBleak monitorはnotify受信できる一方、M3では初期readの正常復帰前に終了するとの報告があった。monitorのソースはリポジトリ未収録なので、行単位の比較はできていない。報告された成功経路と比較し、M3が購読前に課していたreadを除いた。

Bleakの `start_notify()` APIには事前readの要件がない。一方、BLE MIDI 1.0 §5には接続時の初期readが記載されている。今回の変更はすべてのBLE MIDI機器でreadが不要との判断ではなく、既にnotify受信できているUNO Q/iPhoneのM3検証経路を優先するもの。readが失敗・中断したBlueZ/ATTレベルの原因はまだ確定していない。

旧フローには別の問題もあった。`async with BleakClient(...)` 内の例外が呼び出し元へ伝わる前に `__aexit__` の切断処理が実行され、切断callbackが `stopped` を立てる。そこで `FIRST_COMPLETED` がstop waiterだけを返すと、まだ切断処理中の受信taskをcancelし、元の例外をcleanupの `gather(return_exceptions=True)` で捨てる可能性があった。

修正では、BLE処理の例外をコンテキスト終了前に記録する。FIRST_COMPLETEDの結果だけに依存せず記録済み例外を再送出する。ローカルcleanup中の切断callbackは終了理由を上書きしない。パーサ/リレー異常、queue overflow、signalも識別する。異常はtracebackと非ゼロ終了、BLE切断・signalは理由付きの通常終了となる。SIGKILLや電源断はログを残せない。

通常の進行ログ：

```text
Relay connected; requesting initial LED OFF
BLE connecting: ...
BLE connected; validating MIDI service
BLE starting notify: 7772e5db-3868-4112-a1a9-f2669d106bf3
BLE MIDI subscribed: ...
```

停止時は `Stop requested: ...` と `Session ended: <理由>; ...` を確認する。例：`BLE disconnected during notify subscription`、`BLE notify subscription failed: ...`、`MIDI/relay worker failed: ...`、`signal SIGTERM`。`Session wakeup` は完了したtask名を補助的に表示するもので、原因は理由ログとtracebackで判断する。

今回の更新にApp Labコード・MCUスケッチの差分はない。動作確認済みM3リレーをRunしたまま、旧受信プロセスとmonitorを停止して、UNO Qホストのリポジトリルートで実行する：

```bash
git pull --ff-only
source ~/blemidi/bin/activate
python -m unittest discover -s tests -v
python -m m3.receiver \
  --address 9C:C3:94:81:01:53 \
  --socket /home/arduino/ArduinoApps/m3-ble-to-led/m3-led.sock \
  --raw
```

`BLE MIDI subscribed` 到達後、単音・和音・曲末・Ctrl+CのLEDを再確認する。再現する場合は同じコマンドの先頭を `BLEAK_LOGGING=1 python` にし、接続開始から終了理由・tracebackまで保存する。修正後のUNO Q実機検証は利用者側で実施する。

## 参照した一次資料

- [Arduino公式 Blink LED from Python](https://github.com/arduino/app-bricks-examples/tree/main/core-and-foundational/01-led-blink/03-blinking-an-led-from-python) — App.run、Bridge呼び出し、LED極性。スケッチのMPL-2.0表示を保持。
- [Arduino App CLI Compose生成](https://github.com/arduino/arduino-app-cli/blob/main/internal/orchestrator/compose_template.go) — ホストAppディレクトリの `/app` 共有。2026-09-10にmainを確認。
- [Arduino Python Bridge](https://github.com/arduino/app-bricks-py/blob/main/src/arduino/app_utils/bridge.py) — 同期callとエラー。
- [Bleak 3.0.2 client API](https://bleak.readthedocs.io/en/latest/api/client.html) — サービス参照、read、notify、切断callback。
- [MIDI Association BLE MIDI 1.0](https://midi.org/midi-over-bluetooth-low-energy-ble-midi) / [MMA RP-052仕様書の公開コピー](https://www.hangar42.nl/wp-content/uploads/2017/10/BLE-MIDI-spec.pdf) — §7/8のpacket framing、Running Status、SysEx規則。
