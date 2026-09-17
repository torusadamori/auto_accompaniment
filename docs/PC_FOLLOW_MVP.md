# 第二MVP: MIDI入力へのリアルタイム追従

作業先は `D:\GitProjects\Music\auto_accompaniment`。
第一MVPの `play` は固定コード進行のまま利用でき、`follow` は固定進行を参照しません。
UNO Q、BLE、実機駆動関連コードの変更はありません。

## 起動

```powershell
Set-Location D:\GitProjects\Music\auto_accompaniment
# 初回のみ（.venvがなければ先に python -m venv .venv）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m autoaccomp.main ports
.\.venv\Scripts\python.exe -m autoaccomp.main follow --input "MIDIFlex4 0" --output "Microsoft GS Wavetable Synth 0"
```

MIDIFlex物理ポート1はこのPCで `MIDIFlex4 0` と表示されます。
ポート名が変わった場合は `ports` で確認してください。停止はCtrl+C。
入力音はch1へ即時転送し、コード伴奏はch2、ベースはch3です。
`--tempo 100`、`--bars 16`、`--no-bass`、`--no-comping` を指定できます。
既定は120 BPM / 4/4、無限実行。最初の有効コードを認識するまでは伴奏を発音しません。
有限小節指定の計測はアプリ開始時からです。

## 認識と切替

- 全入力チャンネルの物理的な押鍵を、チャンネル・ノート番号の組で管理します。
- Note On velocity=0はNote Offとして扱います。重複Note Onは冪等です。
- オクターブ違いの同音をまとめたピッチクラス集合を、12ルート×5種類に完全一致で照合します。
- Major / Minor / Dominant 7 / Major 7 / Minor 7を認識します。転回形でも同じ結果です。
- 黒鍵ルートはDb / Eb / Gb / Ab / Bb表記です。
- 最初のNote Onから80msを収集窓とし、その間に届くNote Onを集めて判定します。
  後続のNote Onで窓を延長しません。窓内で離鍵した短い音も収集結果に残します。
  窓の開始時に押されている音も含めます。重なった旧コードで一致しない場合は
  現在の押鍵集合でも照合します。結果が変わったときだけ認識結果を表示します。
- 確定後、次の4分音符境界で伴奏・ベースを同時に変更します。
  120 BPMでは最初のNote Onから80ms＋最大約500ms（音源遅延やOSの停止時間を除く）。
- 切替時は旧伴奏の予約を破棄し、発音中の旧伴奏だけを消音します。入力音には触れません。
  新コードのルートベースとコンピングから再開します。
- 全離鍵やUnknownでは最後の有効コードを保持して伴奏を継続します。
  Note Offだけで別コードへ再判定しません。次の有効コードが認識されるまで変更しません。
  単音・二音だけからコードを推定することはありません。停止はCtrl+Cです。
- `Held notes: [60, 64, 67]` は現在の物理押鍵、`Collected notes: [...]` は収集窓の音、
  `Detected chord: C` は判定結果、`Active accompaniment chord: C` は実際に使う伴奏コードです。
  Held notesは変化時のみ、最大約12.5回/秒。他の表示も同じ内容の連続表示を抑えます。

サステインペダルは入力音源へ転送しますが、認識には物理的に押している鍵盤だけを使います。
全離鍵後も最後の有効コードの伴奏が続きます。
CC120/123は対象入力チャンネルの押鍵をクリアします。
コード認識に不要な追加メロディ音も押すと、集合が一致せずUnknownになる場合があります。
今回の評価では両手のコード入力を基本にしてください。

## 伴奏

既存のコンピング・ベース生成器を再利用し、1拍分ずつ予約します。
Major三和音はmaj7、Minor三和音はm7相当の色付けを行い、
ルートを省いた3・5・7度のボイシングを小さな移動でつなぎます。
表示上の認識コードは入力どおりC / Amなどです。
ベースは新コードのルートから開始し、その後3度・5度・半音接続音を使います。
次のコードは未知なので、半音接続は現在のコードのルートへの接続です。
将来のコード予測・単音メロディからの推定・表情への反応はありません。

## 実演テスト

各コードを2〜4拍保持し、以下を順に弾いてください。

| 入力 | 期待表示 |
| --- | --- |
| C E G | C |
| A C E | Am |
| D F A | Dm |
| G B D F | G7 |
| E G C（転回形） | C |
| C E G B | Cmaj7 |
| D F A C | Dm7 |

表示だけでなく、次拍でベースとコード伴奏が一緒に切り替わること、
入力音はすぐ聞こえること、離鍵すると入力音は止まり伴奏は続くこと、
Ctrl+Cで全音が止まることを確認してください。
最終的な成功判定は「伴奏がついてくる」という演奏感の評価です。

## 伴奏出力の検証モード

音程の知識がなくても、認識結果と送信した伴奏ノートの変化を確認できます。

```powershell
.\.venv\Scripts\python.exe -m autoaccomp.main follow --input "MIDIFlex4 0" --output "Microsoft GS Wavetable Synth 0" --debug-accomp
```

コード変更後の最初の拍だけ、ルートから積んだ和音（velocity 78）と
ルートベース（velocity 84）を鳴らします。その後は既存のコンピングとウォーキングベースです。
通常モードのボイシング・音量・入力転送・80ms収集・有効コード保持は変わりません。
`--no-bass` / `--no-comping` によるミュートも有効です。

次のコードを順に弾き、離鍵後も保持して各コードの最初の発音を確認してください。

| 入力 | Detected / Active | 最初のComping notes | 最初のBass note |
| --- | --- | --- | --- |
| C E G | C | [60, 64, 67] | 36 |
| D F A | Dm | [62, 65, 69] | 38 |
| E G B | Em | [64, 67, 71] | 40 |
| F A C | F | [65, 69, 72] | 41 |
| G B D | G | [67, 71, 74] | 43 |

表示例:

```text
Detected chord: Dm
Active accompaniment chord: Dm
Accompaniment output: Dm (beat 5.00)
Comping notes: [62, 65, 69]
Bass note: 38
```

ノート番号は予告値ではなく、MIDI出力のsendが成功したNote Onから記録します。
同時発音の和音はまとめ、発音のないループやNote Offでは表示しません。
遅延で省略された音・ミュートされた音・送信失敗した音も送信済みとして表示しません。
`Accompaniment output` のコードはその発音を生成した伴奏コードです。
新コード認識から次拍までの間は、直前のコードの出力が表示される場合があります。
MIDI送信の成功はスピーカーからの実音を保証しないため、音源の音量と受信設定も確認してください。

## 自動検証

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

第二MVPのテストは、全ルートと5種類の転回形、重複音、velocity=0、ペダル、
指定のC→Am→Dm→G7の次拍切替、両伴奏パート、入力転送、未知コード・離鍵時の継続、
旧予約の破棄、遅延時の発音抑制、キューの上限、割り込み消音を検証します。
実機の入力信号や音源の実音・レイテンシ、演奏感は自動テストでは確認できません。
18msずつずれた押鍵と収集終了前の離鍵、0/35/70msの押鍵、収集窓が延長されないこと、
第7音を離しただけで三和音へ誤切替しないことも検証します。
