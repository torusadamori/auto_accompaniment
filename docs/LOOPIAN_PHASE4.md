# Loopian Phase 4 — 実曲SMFを演奏素材へ

SMF type 0/1（PPQN）を解析し、メロディ候補・和声・キー・曲位置を作ります。
自動判定はヒューリスティックです。confidenceは確率ではなく判定の目安で、手動指定を優先できます。
MIDIの時刻通りの自動再生はしません。曲位置と発音はこれまで通り人間のNote Onで進みます。

## 実曲の確認と演奏

MIDIを `examples/song.mid` に置き、リポジトリのルートでまず解析します。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main analyze-midi --midi-file examples/song.mid
```

選ばれたtrack/channelとコードを確認して演奏します。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/song.mid --loopian-mode melody-flow --tone-priority chord --flow-strength 0.5 --phrase-gap-ms 700 --seed 1 --debug-loopian
```

メロディが違う場合は両コマンドに `--melody-track 3`（0始まり）、または `--melody-channel 1`（1始まり）を追加します。
両方指定すれば両条件で選択します。複数channelが残る場合は最高スコアの一組を使い、混合しません。
コードが違う場合は `--chords Cmaj7 Am7 Dm7 G7` で1小節1コードの循環進行に置き換えられます。
既存のdirect / transformと `flow-strength=0` の互換経路も利用できます。

## 解析モデル

`autoaccomp/midi_analysis.py` の不変データモデルに以下を保持します。

- MIDI type、PPQN、全track名、channel使用状況、program changeと各ノートのprogram
- 全ノートのtick、開始beat、音価、音高、velocity、track/channel、小節・拍位置
- 全tempo mapとtime signature map（未指定時は120 BPM、4/4）
- track/channel別の音数、音域、平均音高、密度、平均/最大同時発音数、重なり率、velocity変動、メロディスコア
- 選ばれたメロディの相対音程、IOI、休符長、フレーズ境界
- コード区間、区間confidence、判定元、キー候補とconfidence

同一音高のNote On/Offはtrack/channelごとのFIFOで対応します。velocity=0はNote Offです。
Note Offの欠けたノートはtrack終端で閉じ、警告を表示します。重なった音や同時音は元イベント順を保持します。
自動単旋律化・トップノート抽出はしないため、手動でピアノ和音trackを選ぶと複数音が素材の連続イベントになります。
SMPTEと非同期type 2は対象外です。ファイル取得・音源分離・音声解析は行いません。

永続キャッシュは未実装です。解析と演奏を分離し、区間の検索索引はメモリ内にキャッシュしています。
旧 `load_melody` APIはPhase 2の厳格な選択・4/4制約を維持し、現在のCLIは `analyze_midi` を使います。

## メロディ候補とconfidence

track/channelの組ごとに採点します。単旋律、重なりが少ない、中〜高音域、適度な密度、velocityの変動を加点します。
`melody / lead / vocal / voice / solo / sax / trumpet / flute` の名前、管楽器系のGM programも加点します。
高いpolyphony、低音中心、極端な持続・短音反復、`bass / drum / piano / guitar / chord / accomp / pad` の名前を減点します。
polyphonyは「ノート音価の合計÷何かが鳴っている時間」、密度は選択パートの範囲内での音数/四分音符beatです。

自動選択は最上位を使用し、上位5候補を表示します。スコア4以上かつ上位差1.25以上（候補一つの場合を含む）ならHIGH、それ以外はLOWです。
LOWでも停止しません。手動指定はMANUALと表示します。
GM channel 10とdrum/percussion名のパートは自動メロディ候補と和声・キー解析から除外します。
既存互換のため `--melody-channel 10` の明示指定は可能ですが、打楽器音高は和声推定に使いません。

## 和声・キー

優先順位は次の通りです。

1. 明示的な `--chords`
2. MIDI marker/textの `Chord: Cmaj7` または単独の `Cmaj7` など
3. 伴奏トラック群からの自動推定
4. 低信頼度なら前コード継続。前コードもなければ推定キーのスケール

メロディとして選んだtrack/channelと打楽器を除いた音群を分析します。伴奏がなければ非打楽器のメロディ自身を分析しますが、単音だけの区間はコードを断定しません。
各拍（拍子の分母単位）で、区間をまたぐ長音も重なり時間で集計します。
コードトーンcoverage、構成音の揃い方、root支持、最低音、velocity、強拍のattack、前コードとの連続性を評価し、コード外音と欠落構成音を減点します。
最低音はrootに小さな加点を与えるだけなので、C/Eのような転回形をE rootと決め打ちしません。

全12 rootについて次の8種類を候補にします。

| 種類 | 表記例 |
|---|---|
| Major / Minor | C / Am |
| Dominant7 / Maj7 / Min7 | G7 / Cmaj7 / Dm7 |
| Dim / Half-diminished / Sus4 | Bdim / Bm7b5 / Csus4 |

3種類未満のpitch classしかない、coverage不足、confidence 0.65未満では新コードを確定しません。
前後が同じコードで挟まれた2四分音符beat未満の短い変化を平滑化し、瞬間的な揺れを抑えます。
各区間にはconfidenceと `automatic / smoothed / previous / marker / manual / key-scale` の判定元を保持します。
MIDIの明示コードマーカーは次のマーカーまで継続します。未対応の `Chord:` マーカーは警告して自動推定へ回します。
拡張コード、slash表記、調の転調区間推定は未対応です。

キーは非打楽器の曲全体のduration/velocity加重pitch-class histogramを、12 rootのmajor/minorテンプレートで評価します。
fallback時はmajorまたはnatural minorの7音を使用し、コードトーンを偽って設定しません。
デバッグに `Harmony fallback: key-scale` と表示します。既知のCmaj7/Dm7/G7のPRIMARY/SECONDARYは従来通りです。
他のコードも構成音をPRIMARYにし、安全な補助音をSECONDARYにします。三和音ではPRIMARYは3音です。

## 拍子・フレーズ・音域

4/4以外、拍子変更、複合拍子の小節・拍位置を保持します。ソースの絶対beatは四分音符単位、表示の拍位置は分母単位です。
例：6/8で1.5四分音符beatは第4拍。強拍は4/4の1・3、6/8の1・4などを参照します。
拍子が小節途中で変わる場合は、その位置で前の不完全小節を閉じて新小節を始めます。同じ拍子の再通知では区切りません。
テンポ変更は素材位置に応じて参照しますが、人間に元のテンポや音価を強制しません。
`--bars` は従来互換の実時間停止上限（起動時テンポで1単位4四分音符beat）です。素材の何小節目まで弾くかを指定する機能ではありません。

`phrase_boundary(rest, weak=0.5, strong=1.0)` で休符からWEAK/STRONGを分類します。
FLOWで強い境界の先を選ぶ場合はコスト3、弱い境界は0.75を加えます。1回で複数の強い境界を飛び越えません。
境界直後の素材位置から前のフレーズへ保持選択することも抑止します。
既存の人間の休止 `--phrase-gap-ms 700` と音域端の再配置は維持します。

`--output-low 48 --output-high 96` は既存の `--note-min/--note-max`、`--range-low/--range-high` と同じ設定です。
transform/flowは元メロディの中央値が出力中央に近づくよう、全体を12半音単位で移します。
オフセットは全素材がMIDI 0〜127を越えない範囲に限定し、個々の最終出力は通常の音域制限で扱います。
directは比較のため原音高を保持します。デバッグには元の中央値、適用shift、元音と移動後の素材音を表示します。

出力velocityは既定で人間の入力値です。任意の `--source-accent 0.3` などで元素材のアクセントを弱く加えられます。
範囲は0〜1、補正は最大±6、最終値は1〜127です。velocity=0入力を発音へ変えることはありません。

## 自作テストMIDIと出力例

著作権上の借用のない、短いオリジナル素材を同梱しています。

- `examples/loopian_type0.mid`: 1 track内のメロディ・和音をchannel別に判別。
- `examples/loopian_type1.mid`: 複数track、3/4→4/4、100→120 BPM、同点のメロディ候補でLOW表示。
- `examples/loopian_gm.mid`: Alto Sax、ピアノ、ベース、ドラム。マーカーなしで4小節のコードを推定。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main analyze-midi --midi-file examples/loopian_gm.mid
```

出力抜粋:

```text
Selected melody: Track 1, channel 1; program: Alto Sax
Melody detection confidence: HIGH
Estimated key: C major; Confidence: 0.52
Harmony analysis: 4 bars; average confidence: 0.99
  Bar 1 beat 1: Cmaj7; Confidence: 0.99; source=automatic
  Bar 2 beat 1: Am7; Confidence: 0.99; source=automatic
  Bar 3 beat 1: Dm7; Confidence: 0.99; source=automatic
  Bar 4 beat 1: G7; Confidence: 1.00; source=automatic
```

再生成と全テスト:

```powershell
.\.venv\Scripts\python.exe scripts/generate_loopian_real_demos.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

実曲ファイルによる聴感の維持、選ばれたメロディの妥当性、コード変化、フレーズ感、末尾ループの自然さは実鍵盤で確認してください。
