# 丹匠ホールディングス Notion 読み取り連携

栗原さんが Notion に自由に書いたものを、アゲル側の AI が読めるようにするための取得ツール。
**読み取り専用**。Notion 側への書き込み・更新は一切行わない。

## 構成

| 項目 | 値 |
|---|---|
| インテグレーション名 | 丹匠AI（内部インテグレーション / 読み取りのみ） |
| bot ID | `3cd9fb771263812d82cc002763e1f9ec` |
| ワークスペース | KuriharaYusukeさんのNotion |
| 親ページ | 丹匠ホールディングス 🛰️ `3839fb77126380b5a024e861951398d1` |
| 読み取り範囲 | 親ページ配下すべて（自動。新規ページも共有設定不要で読める） |

栗原さんは親ページの下に好きな構造でページを足すだけでよい。型は不要。
整理・構造化はアゲル側で行う。

## なぜスクリプト経由なのか

Claude の Notion コネクタは OAuth 専用で、認証時に選んだ **1 ワークスペースに固定**される。
アゲル側は `Ageru Inc` ワークスペースに繋がっているため、栗原さんのワークスペースは
コネクタからは見えない。内部インテグレーションのトークンを使う API 経由が唯一の道になる。

## トークンの置き場所

スクリプトは次の順で探す。

1. 環境変数 `NOTION_TANBA_TOKEN`
2. `~/.notion_tanba_token`（1 行、パーミッション 600）

```bash
umask 077
printf '%s' 'ntn_xxxxxxxx' > ~/.notion_tanba_token
chmod 600 ~/.notion_tanba_token
```

リモートセッションはコンテナが毎回作り直されるため、恒常運用するなら
Claude Code の環境設定（claude.ai/code の Environment → 環境変数）に
`NOTION_TANBA_TOKEN` を登録するのが確実。

**トークンをこのリポジトリにコミットしない。`.gitignore` で保護済み。**

## 使い方

```bash
python3 tools/notion_tanba/fetch_notion.py --list     # 階層を一覧するだけ（取得しない）
python3 tools/notion_tanba/fetch_notion.py            # 差分取得
python3 tools/notion_tanba/fetch_notion.py --full     # 全件取り直し
python3 tools/notion_tanba/fetch_notion.py --out DIR  # 出力先変更
```

- 依存ライブラリなし（Python 3 標準ライブラリのみ）
- 既定の出力先は **`~/notion_tanba_export`（リポジトリ外）**
- `_manifest.json` に `last_edited_time` を記録し、2 回目以降は変更ページのみ取得
- Notion のレート制限（平均 3req/s）に合わせて待機。429 / 5xx は自動リトライ

出力は Notion の階層をそのままミラーした Markdown。各ファイル先頭に
`notion_id` / `url` / `last_edited` を持つので、元ページへ即座に辿れる。

## ⚠️ 取得データの取り扱い

このリポジトリは **public** かつ **GitHub Pages 有効**（`docs/` を配信）。
取得データには賃金テーブル、請求書、人員計画、事業計画書、取引先名などが含まれる。

- 出力先は既定でリポジトリ外。**`docs/` 配下には絶対に置かない**
- 要約・分析結果をリポジトリに置く場合も、機微情報が残っていないか目視確認する
- 公開したい成果物がある場合は、private リポジトリを別途用意すること

## トークンのローテーション手順

トークンが漏れた疑いがある場合（チャットや画面共有に映った等）は即座に再発行する。

1. https://www.notion.so/profile/integrations を開く
2. 「丹匠AI」を選択
3. Internal Integration Secret の「表示」→「再生成 (Rotate)」
4. 新しいトークンを `~/.notion_tanba_token` と環境変数側に反映
5. 古いトークンは自動的に無効化される

インテグレーション自体を止める場合は同じ画面から削除するか、
親ページの「…」→ 接続 → 丹匠AI を解除する。
