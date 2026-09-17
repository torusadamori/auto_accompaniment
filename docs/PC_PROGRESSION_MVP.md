# 第四MVP: 自然な進行と安定性を優先

第三MVPの挙動は変更せず、`--progression-aware` で別の推定器を選びます。
候補は引き続きC / Dm / Em / F / G / Am、履歴4拍、C major固定です。

## 起動と比較

```powershell
Set-Location D:\GitProjects\Music\auto_accompaniment
.\.venv\Scripts\python.exe -m autoaccomp.main ports
# 第四MVP
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --progression-aware --debug-harmony
# 第三MVPとの比較（前の実行はCtrl+Cで停止）
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --debug-harmony
```

入力ポート名の末尾の番号は接続順で変わります。`ports` の結果に合わせて指定してください。
`--tempo` / `--bars` / `--no-comping` / `--no-bass` / `--debug-accomp` も従来どおり使えます。
`--debug-accomp` は変更拍の発音を強調するため、音楽性の比較時は外してください。

## 伴奏が聞こえない場合の送出確認

`--debug-harmony` は推定ログだけです。実送信ログには `--debug-accomp` を併用してください。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --progression-aware --debug-harmony --debug-accomp
```

検証時の音色はch1 Piano、ch2 Electric Piano（GM program 4、0始まり）、ch3 Acoustic Bass。
チャンネル音量は88/108/112、Expressionは127に設定し、コンピングと低いベースを区別しやすくします。
オプションなしの音色設定は従来どおりです。`play` / `follow` の設定には変更ありません。
`--debug-accomp` は変更拍の発音も強調するため、通常の音楽性比較では外してください。

実行開始時に出力先、各パートのチャンネル・音色、ミュート有無を表示します。
伴奏発音時はsend成功後の `Accompaniment output` / `Comping notes` / `Bass note` が表示されます。
Ctrl+Cまたは有限小節終了時には、成功したNote On件数を次の形式で表示します。

```text
Sent MIDI Note On: Melody=14 Comping=21 Bass=15
```

数値は演奏によって変わります。Comping/Bassが0なら、推定コードの確定、ミュート指定、
`Late attacks skipped` を確認してください。3パートの件数が増えているのに聞こえない場合は、
Windowsの再生先・音量と音源側の受信設定を確認してください。
有効コードを一度入力してから離鍵すると、メロディだけ止まり伴奏は続くため区別しやすくなります。

コード上の経路は以下です。

```text
main.melody_accompaniment
 → forward_pending（入力スルーもここで送信）
 → MelodyFollower.receive → MelodyHistory
 → MelodyFollower.tick → ProgressionEstimator.estimate
 → engine.detected → Follower.tick_accompaniment
 → comping.generate / walking_bass.generate → Scheduler.add / tick
 → DebugOutput.send → AuditedOutput.send → MIDIポート.send
```

表示はポート送信成功の確認であり、スピーカーからの実音を録音して検証したものではありません。

## メロディを消して伴奏だけ確認

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --progression-aware --debug-harmony --debug-accomp --mute-melody
```

`--mute-melody` は入力をコード推定に使いながら、MIDIスルーだけを無効化します。
入力ノートやペダルなどは音源へ転送せず、自動コンピングとベースだけを送ります。
伴奏用の音色設定・終了時の消音は従来どおり送信します。
`--debug-accomp` 併用時の音量はコンピング108、ベース112で、送信ノート表示も維持します。
終了時の件数は `Melody=0 Comping=... Bass=...` になります。

起動時の `Melody thru: MUTED` を確認して一本指で弾いてください。
自分の押鍵音がなく、推定コードに応じた和音と低音だけが鳴るのが期待動作です。
鍵盤本体の内蔵音源や別アプリが直接鳴らす音は、このオプションの制御対象外です。
外すと通常の即時スルーに戻ります。play/followには追加していません。

## 採点ルール

総合点は `melody + transition + hold + bar` です。

- melody: コードトーン3点、その他のC major音0.5点、スケール外-1点の重み付き平均。
  重みは履歴窓内の音価の1.25乗×強拍係数×新しさ。
  強拍係数は1拍目2.0、3拍目1.5、その他1.0（拍の前後0.2拍を許容）。
- transition: 下表の進行を少し優遇。それ以外の変更は-0.15、同じコードは0。
- hold: 現在のコードに+0.25。
- bar: 小節頭では変更先に+0.10。途中では現コードに+0.20。

| 直前コード | 優遇する次コードと点数 |
| --- | --- |
| C | F +0.30 / G +0.30 / Am +0.25 |
| Dm | G +0.45 / Am +0.25 |
| Em | Am +0.30 / F +0.30 |
| F | G +0.45 / C +0.30 / Dm +0.25 |
| G | C +0.45 / Am +0.25 |
| Am | Dm +0.45 / F +0.30 / G +0.30 |

最低2拍保持と変更しきい値0.35を維持します。さらに選択候補のメロディ点が
メロディ最高点から0.40を超えて低ければ変更しません。
進行だけでコードを動かさず、根拠の強いメロディなら小節途中や優遇外への変更も可能です。
Dm→G→Cなどは各遷移を優遇することで選びやすくしますが、進行を強制しません。
小節位置はアプリの拍時計によるもので、演奏者の拍・テンポへの自動同期はありません。

## 休符・表示

新しい入力も音価の更新もなければ、候補の再計算とデバッグ表示を行いません。
短い音が拍前に離鍵された場合は、次拍に一度処理してから休符中の再計算を止めます。
長い音を押している間は長さが変わるので毎拍評価します。サステインだけの保持は含みません。
伴奏・ベースは最後のコードで継続します。停止はCtrl+Cです。

詳細表示では、採点時の `Current chord` と各候補の
`melody / transition / hold / bar / total` を表示します。
`Estimated chord` と `Active accompaniment chord` は変更時のみです。
最低保持時間やしきい値により、総合点の最高候補でも切り替わらないことがあります。
通常実行では `--debug-harmony` を外すと簡潔な表示になります。

## 実鍵盤での評価

両方式で同じテンポ・同じ長さを意識し、一本指で次を弾いて比較してください。

```text
C C G G A A G
F F E E D D C
```

1. コードが短時間に行き来せず、2拍〜1小節程度のまとまりに聞こえるか。
2. 数小節休んでもコードが動かず、候補表示が止まるか。
3. 小節頭に長く弾いた音が推定へ反映されるか。
4. EstimatedとActiveが一致し、コンピングとベースが追従するか。
5. 従来方式より流れが自然か。安定しすぎて必要な変更が遅く感じられないか。

これは和声の唯一の正解を保証する機能ではありません。実機の聴感評価は別途必要です。
推定器は `progression_estimator.py` に独立し、第三MVPの `harmony_estimator.py` はそのままです。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
