# Loopian Phase 2 — MIDIメロディを手で進める

素材位置周辺の探索と弱い変奏を加えた `melody-flow` は [Phase 3](LOOPIAN_PHASE3.md) を参照してください。
この文書のdirect / transformは従来のまま利用できます。
実曲の自動選択・和声推定・拍子変更は [Phase 4](LOOPIAN_PHASE4.md) で追加しました。以下の旧解析制限は低レベル互換APIについての説明で、現在のCLIはPhase 4の解析層を使用します。

既存MIDIは演奏素材です。入力がなければ発音もメロディ位置の進行もありません。
velocityが正のNote Onを1回受けるたびに、素材をちょうど1音消費します。
Note Offとvelocity=0のNote Onは音を解放するだけです。
入力の絶対音高は出力へ転送しません。発音タイミング、velocity、音を離すタイミングは人間が決めます。
素材の最終音の次は先頭に戻ります。休止後は次の未演奏素材から続けます。
CC120/123で解放・予約取消・素材位置と入力ジェスチャーの初期化を行います。

## 実鍵盤比較

リポジトリのルートで実行します。各実行はCtrl+Cで終了します。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/loopian_phase2.mid --melody-track 1 --loopian-mode melody-direct --debug-loopian
```

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main loopian --input "MIDIFlex4 1" --output "Microsoft GS Wavetable Synth 0" --midi-file examples/loopian_phase2.mid --melody-track 1 --loopian-mode melody-transform --tone-priority chord --phrase-gap-ms 700 --debug-loopian
```

- direct: 同じ鍵でも任意の鍵でも、押すたびに元メロディの次の音高を出します。コード吸着・音域制限・方向変形・休止後の再配置を行いません。
- transform: 上昇→下降→上昇を入力し、元メロディの動きとコード感が残りながら、自分の方向に反応するか聴きます。SAMEは基本的に直前出力を保ちながら素材位置を進めます。
- 速く・遅く・休止を交えて弾き、入力なしでは進まず、Note Offでも素材が進まないことを確認します。
- 長いUP/DOWN、短いタップ、同じ鍵の重複、ペダル、Ctrl+Cで音が残らないことも確認します。

MIDIを指定すると既定モードは `melody-transform`、既定の `--grid` は0です。
ファイルを指定しない場合は従来の `gesture` モード、grid 16になります。
Phase 2でも `--grid 8/16` を明示するとPhase 1の小さな未来方向の補正を適用します。
入力間隔（`Gesture.elapsed`）を保持しますが、元MIDIの音価で自動発音・自動解放はしません。

## MIDI選択と保持情報

MidoでSMF type 0/1を読み込みます。PPQNのtickをトラックごとに累積し、四分音符単位のbeatへ変換します。
Note On/Offは `(track, channel, note)` ごとのFIFOで対応付けます。
元のtick・beat、音価、velocity、track/channel、直前素材からの半音差、直前までの発音終了からの休符長を保持します。
テンポ変化、拍子、コードマーカーはメロディ選択に関係なく全トラックから取得します。

- `--melody-track 1`: **0始まり**のトラック番号。付属MIDIはtrack 0がメタ情報、track 1がメロディです。
- `--melody-channel 1`: **1始まり**のチャンネル番号。両方指定すれば積集合です。
- 無指定で非打楽器ノートのあるトラックが一つなら選択。複数あれば候補番号・名前を表示して指定を求めます。高度な自動判定はしません。
- チャンネル10は通常除外します。明示的な `--melody-channel 10` の場合だけ素材にできます。
- 同時和音・重なりは保持し、同tickならtrack番号・元イベント順で1音ずつ進めます。トップノート抽出ではないため、単音メロディトラックを推奨します。
- 空の選択、対応Note Offのないノート、SMF type 2、SMPTE、4/4以外はハードウェアを開く前にエラーにします。

## コードと拍の進め方

Phase 2のコード・強拍・テンポ参照位置は、**今回消費する素材のbeat** です。
ゆっくり弾いてもコードだけ時計で先へ進みません。休止・音域端の待機でも同じ素材を保持します。
Phase 1の時計で進む和声は変更していません。

SMFには一般共通のコードイベント形式がないため、このMVPではmarker/textの `Chord: Cmaj7`、`Chord: Dm7`、`Chord: G7` を読みます。
マーカーのコードは次のマーカーまで継続します。ファイル末尾では先頭の文脈へ戻ります。
コード情報がない場合は1小節1コードの `Cmaj7 Dm7 G7 Cmaj7` を使います。
`--chords Dm7 G7 Cmaj7 Cmaj7` の明示指定はマーカーより優先する、繰り返しの小節単位進行です。
コード推定、他のコード形式の解析、調判定は行わず、Phase 1と同じC majorの許可音集合を使います。

`--tempo` は全素材のテンポ参照を上書きします。無指定ではMIDIのテンポ変化を保持・参照し、未指定区間は120 BPMです。
テンポはgrid補正の間隔に使いますが、MIDIファイルを時間通り再生することはありません。
`--bars N` は従来同様の実時間の実行上限です。Phase 2では起動時テンポで換算し、N小節分の時間で入力の有無によらず停止します。

## transformの選音

PRIMARY / SECONDARYと、強拍・コード変更後2音の優先度はPhase 1を引き継ぎます。

| コード | PRIMARY | SECONDARY |
|---|---|---|
| Cmaj7 | C E G B | D A |
| Dm7 | D F A C | E G |
| G7 | G B D F | A E |

1. STARTは元メロディ初音付近のPRIMARYへ吸着します。
2. UP/DOWNは直前出力の上/下側の許可音だけを候補にします。
3. 元の相対音程の大きさを1〜5半音へ収め、人間の方向を付けて「輪郭目標」を作ります。元の上昇にDOWNを入力したら下降に反映します。
4. `輪郭目標との距離 + 0.25 × 元音とのピッチクラス距離 + 0.15 × 前出力との距離 + SECONDARYペナルティ` の最小候補を選びます。同点はPRIMARY・距離・低い音の順です。
5. SECONDARYペナルティは通常1.5、素材のbeat 1/3先頭0.25拍またはコード変更後2音で4。通常の移動は5半音以内に限定します。方向側にPRIMARYがなくてもSECONDARYで続けます。

元メロディの音名だけでなく相対音程が候補に効くため、C→Eの4半音の動きをF→Aへ移すこともできます。
元の反復にUP/DOWNを入力した場合は最小1半音の目標を作り、方向を守って動きます。
SAMEは元輪郭を適用せず基本維持し、コード変更直後だけ近いPRIMARYへ移れるPhase 1規則を使います。
`--tone-priority flat` では通常の変形スコアからSECONDARYペナルティを外します。元音高の完全比較には `melody-direct` を使ってください。

`--phrase-gap-ms 700` のINPUT_PAUSEと、方向側の候補がないRANGE_LIMITでは、従来のLOW_MID/HIGH_MID/PREVIOUSへ再配置します。
素材位置は継続し、再配置待ちで二重消費しません。RANGE_LIMITの60msの区切りも維持します。
方向と通常移動の跳躍上限はフレーズ内の保証で、フレーズ境界では音域を移動します。

## デバッグ・テスト素材

`--debug-loopian` には0始まりの素材index、累積Step、元音高・輪郭・tick・beat・音価・track/channel、
入力方向、コード、出力音の役割、選択理由、受信・予定・実発音時刻を表示します。
directのコード外音は `ORIGINAL outside palette` と表示し、吸着しません。

素材は [`examples/loopian_phase2.mid`](../examples/loopian_phase2.mid)。オリジナルの16音、120 BPM、4/4です。

```text
Chord:  Cmaj7     | Dm7       | G7        | Cmaj7
Melody: C4 E4 G4 A4 | F4 A4 C5 D5 | B4 D5 F5 E5 | G4 E4 D4 C4
```

再生成と全自動テスト:

```powershell
.\.venv\Scripts\python.exe scripts/generate_loopian_demo.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自動テストは読み込み、選択、直接演奏、方向・音域・コード吸着、決定性、素材の一度だけの消費、
Note Off、velocity=0、フレーズ待機、実CLIの終了処理、既存モードの回帰を検証します。
「音楽を手で操っている感じ」や元メロディの聴感上の残り方は実鍵盤での評価対象です。
