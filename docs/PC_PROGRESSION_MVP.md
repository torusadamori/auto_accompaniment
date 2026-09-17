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
