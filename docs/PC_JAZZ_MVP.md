# 第五MVP: basic / jazz の比較

`melody-follow --style jazz` で第五MVPを選びます。既定値は `basic` で従来と同じです。
play / follow / UNO Qは変更していません。

```powershell
Set-Location D:\GitProjects\Music\auto_accompaniment
.\.venv\Scripts\python.exe -m autoaccomp.main ports
# 第四MVP相当
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --progression-aware --style basic --seed 1
# 第五MVP（前の実行をCtrl+Cで終了してから）
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --progression-aware --style jazz --seed 1
# 伴奏だけを診断
.\.venv\Scripts\python.exe -m autoaccomp.main melody-follow --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --style jazz --seed 1 --debug-harmony --debug-accomp --mute-melody
```

ポート名は `ports` の結果に合わせます。テンポは既定120 BPM / 4/4。
`--tempo`、`--bars`、`--no-comping`、`--no-bass` は従来どおり使えます。
jazzは第四MVPの進行採点を自動使用するので `--progression-aware` は省略可能。
basicでは同オプションの有無も従来どおりです。seedはjazzのみに影響します。

## コード変更

原則4拍以上保持し、小節頭で変更します。開始直後は最初の推定を次の拍で使います。
3拍目に限り、2拍以上保持済みで、新候補の総合点が0.9以上、メロディ点が0.5以上
高い場合は変更します。1拍ごとの変更は行いません。
次の小節へ変更を予約した場合、`Pending chord` にコードと実行拍を表示します。
予約はそれ以前の新しい入力で取消されることがありますが、実行拍に達した予約は適用します。
休符から新しい推定は行いません。ただし、休符前に決めた予約は予定拍に実行します。

## コンピング

- C/F: 主に3度・長7度・9度。G: 3度・短7度・13度。
- Dm/Am: 短3度・短7度・9度。EmはC major内に収めるため9度の代わりに5度。
- ルートを省き、52〜72の範囲で直前ボイシングに近い転回形を選びます。
  各声部7半音以内の候補がある場合はその範囲に制限します。
- メロディ履歴の平均音高が60未満なら、低い伴奏音へ軽いペナルティを付けて上側を優先。
  厳密な音域衝突回避ではなく、ボイスリーディングとの折り合いです。
- 4パターンをseed付きシャッフルで各4小節に一度ずつ使用し、隣接小節で同じものを繰り返しません。

| パターン | 発音位置（1始まりの拍） |
| --- | --- |
| A | 2、4 |
| B | 1.5、3 |
| C | 2.5、4 |
| D | 1、3.5 |

小節開始時に直近4拍のNote Onが6個以上なら、パターンの発音を2回から1回へ減らします。
疎なメロディ・長音・休符なら2回です。コードが変わってもリズムの小節位置はリセットしません。
そのためコード変更と同時に和音が鳴らず、パターンの次の位置まで待つことがあります。
ベースは変更拍にルートを鳴らします。

## ベース

36〜50で4分音符を生成。ルート、コードトーン、スケール音を候補とし、
音間の距離・大きい跳躍・同音反復にコストを付け、近い候補をseedで選びます。
各小節やコード変更時にルートを提示します。
終端は接続先ルートの半音／全音手前または上側の音を選びます。
4拍目に次拍の変更予約があれば、そのコードへ接続し、次拍で近いオクターブのルートへ着地します。
次コードが未定のときは現在コードのルートを接続先にします。未来の入力を予知しません。

## 再現性とログ

同じseed・同じMIDI入力時刻/音価・同じテンポ・同じオプションで同じ結果になります。
人間が弾き直してタイミングや音数が変わると、推定・密度・seed消費も変わり得ます。

`--debug-accomp` はパターン、予定ボイシングとベース、接続先を表示します。
`planned` は生成予定、`Comping notes` / `Bass note` は実際のsend成功後の表示です。
jazzではこのオプションがボイシング・リズムを検証用の三和音に置き換えることはありません。
診断時のElectric Piano音色・音量調整は従来どおり有効です。
basicのdebug-accompによる変更拍の強調は従来どおり残しています。

## 実鍵盤での確認

同じテンポで `C C G G A A G / F F E E D D C` を弾き、basic/jazzを比較します。
まずdebug-accompなしで音楽性を比較し、聞き分けにくければmute-melodyで伴奏だけにします。

1. コードの変更が落ち着くか。安定しすぎて追従が遅く感じないか。
2. コンピングの裏拍・休み・パターン変化が自然か。
3. ベースの低音が滑らかにつながるか。
4. 細かく弾くとコンピングが薄くなり、メロディを邪魔しにくいか。
5. 低いメロディとの音域の重なりが減るか。

これらの音楽的な成功判定は実演評価が必要です。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
