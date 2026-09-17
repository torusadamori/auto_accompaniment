# Loopian Phase 3 — FLOW的フレーズ変奏

`melody-flow` は元MIDIの小さな探索窓から素材音と出力音を選びます。
入力なしでは発音せず、人間が発音・解放タイミングを決めます。AI生成やLoopian FLOW/QUBITの移植ではありません。
MIDI未指定のPhase 1、Phase 2の `melody-direct` / `melody-transform` は維持します。
MIDI指定時の既定モードもtransformのままなので、FLOWは明示的に指定してください。

## 実鍵盤A/B/C比較

リポジトリのルートで順番に実行します。各回Ctrl+Cで停止してください。

A: 原音高のステップ演奏

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/loopian_phase2.mid --melody-track 1 --loopian-mode melody-direct --debug-loopian
```

B: Phase 2の方向・輪郭変形

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/loopian_phase2.mid --melody-track 1 --loopian-mode melody-transform --tone-priority chord --phrase-gap-ms 700 --debug-loopian
```

C: Phase 3のFLOW変奏

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/loopian_phase2.mid --melody-track 1 --loopian-mode melody-flow --tone-priority chord --flow-window 3 --flow-strength 0.5 --phrase-gap-ms 700 --seed 1 --debug-loopian
```

同じ上下操作で比較し、元メロディとの関係、操作方向、コード感、跳躍の自然さを聴いてください。
FLOWでは小さい入力間隔と6半音以上の入力間隔、速い押鍵とゆっくりした押鍵を比較します。
`--seed 2` で別の変奏、`--flow-strength 0` でtransformと同じ結果、`0.25 / 0.75 / 1` で自由度の差を確認できます。
MIDI・入力音列・入力時刻・設定が同じなら同じseedで同じ結果です。人間の押鍵間隔が変われば速度区分や休止判定も変わります。
seedを省略すると起動ごとに新しい乱数状態になります。実鍵盤での自然さは自動テストとは別に評価してください。

## 探索窓と素材位置

- `--flow-window 3` は次に期待する素材位置（cursor）の前後3音。範囲は1〜8です。
- 窓には音高、contour、現在コードでのrole、小節/拍、duration、velocity、元indexを保持します。
- 過去側は短い輪郭の参照と直前素材の再利用に使います。cursorそのものは逆走しません。
- 選択indexを `cursor - 1 / cursor / cursor + 1 / cursor + 2` とすると、次のcursorはそれぞれ `+0 / +1 / +2 / +3` 進みます。
- 同位置の保持は最大1回連続。次の押鍵では必ず前進します。開始・フレーズ再配置では現在素材を使って+1進みます。
- 小節またはコードが変わる場所を飛び越えません。末尾では元MIDIへ循環し、内部の絶対位置は増え続けます。
- 和声・強拍は探索開始時のcursorの文脈で決めます。探索で選ぶ素材も同じ小節・コード内に限定するので、変奏だけで和声を先取りしません。
- 出力音高と素材位置は別管理です。同じ素材indexでも別の許可音へ変形できます。

Note On受信時に素材位置を予約するPhase 2と違い、FLOWでは発音順に探索・確定します。
Note Off、velocity=0のNote On、音域端の待機で素材を進めません。
`flow-strength=0` のときだけPhase 2の予約・選音経路をそのまま使い、MIDIイベント列まで一致させます。

## 制約とスコア

最初に方向・許可音・音域で候補を制限します。UPは直前出力より上、DOWNは下だけです。
通常の跳躍上限は5半音。LARGEかつFLOW強度0.5以上では7半音まで許可します。
SAMEはPhase 2の維持規則を使い、コード変更直後だけ近いPRIMARYへ移れます。
PRIMARY / SECONDARYの定義はPhase 1/2と同じです。

強拍（素材beat 1/3の先頭0.25拍）とコード変更後2音は、方向と跳躍上限に合うPRIMARYがあればその中に候補を限定します。
さらに3rd/7thに小さなボーナスを付けます。PRIMARYが方向側にない音域端ではSECONDARYで続けられます。
`--tone-priority flat` はコード役割のペナルティとPRIMARY限定を外しますが、許可音集合・方向は保ちます。

候補コストは小さいほど良く、概ね次の和です。

```text
0.75 × 人間の方向に向けた元輪郭目標との距離
+ 0.25 × 元音とのピッチクラス距離
+ 0.15〜0.30 × 直前出力との距離
+ 素材indexの近さ・前進量のコスト
+ SECONDARYペナルティ
+ 短期反復ペナルティ
− contour一致・motif一致・重要音・3rd/7thのボーナス
```

輪郭目標には元の相対音程の大きさを使い、人間がDOWNなら下降へ向けます。
contourと人間方向が一致すると優遇しますが、不一致でも出力方向は人間を守ります。
元の3イベントcontourと直近2入力＋今回の入力方向が一致すればmotifボーナスを加えます。
ゆっくりした入力では素材の音価・velocityの大きい音を少し優先します。元のvelocityは素材評価用で、出力velocityは入力値です。

最後にコスト上位3件まで、かつ最良値との差が `1.5 × flow-strength` 以下の候補だけを残します。
その中から `exp(-コスト差 / (0.2 + 0.6 × flow-strength))` に比例する確率で1件選びます。
ランダムに許可音集合全体から選ぶことはありません。乱数は専用の `random.Random(seed)` で管理します。

## FLOW強度・人間ジェスチャー・速度

`--flow-strength` は0〜1の有限値、既定0.5です。

| 強度 | 素材前進の自由度 |
|---|---|
| 0 | Phase 2と同じ、常に+1 |
| 0より大きく0.25未満 | +1。狭い候補差で弱く選択 |
| 0.25以上 | 同位置、+1、+2 |
| 0.5以上かつLARGE | 上記にまれな+3を追加 |
| 0.75以上 | ジェスチャーの大小によらず+3も許可 |

窓の大きさ、小節/コード境界、フレーズ境界による制限が常に優先されます。
強度は候補差の許容幅、contour/motif、反復抑制にも効きます。1でも方向・コード・跳躍制約は外しません。

| 入力intervalの絶対値 | 区分 | 主な効果 |
|---|---|---|
| 0〜2半音 | SMALL | 近い音を優先、素材の飛ばしを抑える |
| 3〜5半音 | MEDIUM | 中程度 |
| 6半音以上 | LARGE | 強度0.5以上で最大7半音、+2/+3を許容 |

入力intervalをそのまま出力intervalにはしません。
速度はNote On間隔で判定し、FASTは180ms未満、SLOWは400ms以上、それ以外はNORMALです。
FASTは前進量を最大+1とし、弱拍のSECONDARYペナルティを1へ緩めます。
NORMALは1.5、SLOWは2.5。強拍・コード変更直後の優先は速度より上です。

## 短いフレーズ履歴と境界

直近8件の出力音・入力方向・素材indexを保持します。
同音過剰反復、3巡目以降の2音往復、等間隔の階段、直前フレーズとの同じ出だしに弱いペナルティを付けます。
SAMEは反復抑制の対象外です。人間自身がUP/DOWN交互に操作している場合、2音往復ペナルティを小さくします。
次善候補も方向・音域・コードの制約を満たす必要があり、候補が一つしかなければ反復を許します。

`--phrase-gap-ms 700` は維持します。INPUT_PAUSE/RANGE_LIMITで素材位置を継続し、Phase 2と同じ帯域へ再配置します。
そこで現在の短期履歴を直前フレーズとして保存し、新しい履歴を始めます。高度なフレーズ境界推定や長期学習はしません。
RANGE_LIMITの60ms待機中は素材位置・履歴・乱数を消費しません。後続の押鍵も受信順で処理します。
再配置そのものには音域移動があり、方向保証はフレーズ内です。
CC120/123は音の解放と予約取消に加え、素材位置・履歴・乱数を初期状態に戻します。

## デバッグと検証

`--debug-loopian` でcursor、探索窓、元音の情報、入力strength/speed、候補上位6件のcost、
選択indexと出力音、次cursor、contour一致、motif・反復コスト、seedを確認できます。
先頭・再配置はPhase 2の選音なので候補は1件です。強度0は互換経路を使用した旨を表示します。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

テストはseed再現性、局所探索、前進主体、方向・音域・跳躍、強拍/変更後PRIMARY、強度0互換、
自由度、速度、motif、反復抑制、短期履歴、休止、末尾ループ、待機中の乱数・位置不変、
遅れたtickでの押鍵順、velocity=0、CLI、既存モードの回帰を検証します。
