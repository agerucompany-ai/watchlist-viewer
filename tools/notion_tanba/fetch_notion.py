#!/usr/bin/env python3
"""丹匠ホールディングス(栗原) Notion -> ローカル Markdown 書き出し.

読み取り専用。Notion 内部インテグレーション「丹匠AI」のトークンを使う。

  トークン: 環境変数 NOTION_TANBA_TOKEN、なければ ~/.notion_tanba_token
  出力先  : 既定 ~/notion_tanba_export (リポジトリ外。公開リポジトリに入れないこと)

使い方:
  python3 fetch_notion.py                 # 差分のみ取得
  python3 fetch_notion.py --full          # 全件取り直し
  python3 fetch_notion.py --out DIR       # 出力先を変える
  python3 fetch_notion.py --list          # 取得せず一覧だけ表示
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_OUT = Path.home() / "notion_tanba_export"
TOKEN_FILE = Path.home() / ".notion_tanba_token"


def load_token():
    tok = os.environ.get("NOTION_TANBA_TOKEN", "").strip()
    if tok:
        return tok
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    sys.exit(
        "トークンが見つかりません。環境変数 NOTION_TANBA_TOKEN を設定するか、\n"
        f"{TOKEN_FILE} にトークンを保存してください (chmod 600)。"
    )


class Notion:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{API}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    self.calls += 1
                    # Notion のレート制限は平均3req/s
                    time.sleep(0.34)
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(float(e.headers.get("Retry-After", 2)))
                    continue
                if e.code in (500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                detail = e.read().decode("utf-8", "replace")[:400]
                raise SystemExit(f"Notion API {e.code} {method} {path}\n{detail}")
            except urllib.error.URLError:
                time.sleep(2 ** attempt)
        raise SystemExit(f"Notion API リトライ上限 {method} {path}")

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, body):
        return self._request("POST", path, body)

    def paged(self, path, body=None):
        """search / children / query の共通ページング."""
        cursor = None
        while True:
            if body is None:
                sep = "&" if "?" in path else "?"
                page = self.get(path + (f"{sep}start_cursor={cursor}" if cursor else ""))
            else:
                payload = dict(body)
                if cursor:
                    payload["start_cursor"] = cursor
                page = self.post(path, payload)
            for item in page.get("results", []):
                yield item
            if not page.get("has_more"):
                return
            cursor = page.get("next_cursor")


# ---------------------------------------------------------------- rich text


def rich_text(items):
    out = []
    for t in items or []:
        s = t.get("plain_text", "")
        a = t.get("annotations", {})
        if t.get("type") == "equation":
            s = f"${s}$"
        if a.get("code"):
            s = f"`{s}`"
        if a.get("bold"):
            s = f"**{s}**"
        if a.get("italic"):
            s = f"*{s}*"
        if a.get("strikethrough"):
            s = f"~~{s}~~"
        href = t.get("href")
        if href:
            s = f"[{s}]({href})"
        out.append(s)
    return "".join(out)


def title_of(obj):
    if obj.get("object") == "database":
        return rich_text(obj.get("title")) or "(untitled)"
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return rich_text(prop.get("title")) or "(untitled)"
    return "(untitled)"


def prop_to_text(prop):
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return ""
    if t in ("title", "rich_text"):
        return rich_text(v)
    if t in ("number", "url", "email", "phone_number", "created_time", "last_edited_time"):
        return str(v)
    if t == "select":
        return v.get("name", "")
    if t in ("multi_select",):
        return ", ".join(x.get("name", "") for x in v)
    if t == "status":
        return v.get("name", "")
    if t == "date":
        s = v.get("start", "")
        return f"{s} → {v['end']}" if v.get("end") else s
    if t == "checkbox":
        return "✓" if v else ""
    if t == "people":
        return ", ".join(x.get("name", "") for x in v)
    if t == "files":
        return ", ".join(x.get("name", "") for x in v)
    if t == "relation":
        return ", ".join(x.get("id", "") for x in v)
    if t == "formula":
        return str(v.get(v.get("type"), ""))
    if t == "rollup":
        inner = v.get(v.get("type"))
        return str(inner) if not isinstance(inner, list) else f"{len(inner)} items"
    if t in ("created_by", "last_edited_by"):
        return v.get("name", "")
    if t == "unique_id":
        return f"{v.get('prefix') or ''}{v.get('number')}"
    return ""


# ---------------------------------------------------------------- blocks


def blocks_to_md(api, block_id, depth=0, out=None):
    if out is None:
        out = []
    if depth > 8:
        return out
    pad = "  " * depth
    numbering = 0
    for b in api.paged(f"/blocks/{block_id}/children?page_size=100"):
        t = b.get("type")
        v = b.get(t, {}) or {}
        txt = rich_text(v.get("rich_text"))

        if t == "numbered_list_item":
            numbering += 1
        else:
            numbering = 0

        if t == "paragraph":
            out.append(f"{pad}{txt}" if txt else "")
        elif t in ("heading_1", "heading_2", "heading_3"):
            level = int(t[-1])
            out.append("")
            out.append(f"{'#' * (level + depth)} {txt}")
            out.append("")
        elif t == "bulleted_list_item":
            out.append(f"{pad}- {txt}")
        elif t == "numbered_list_item":
            out.append(f"{pad}{numbering}. {txt}")
        elif t == "to_do":
            mark = "x" if v.get("checked") else " "
            out.append(f"{pad}- [{mark}] {txt}")
        elif t == "toggle":
            out.append(f"{pad}- <details> {txt}")
        elif t == "quote":
            out.append(f"{pad}> {txt}")
        elif t == "callout":
            icon = (v.get("icon") or {}).get("emoji", "")
            out.append(f"{pad}> {icon} {txt}".rstrip())
        elif t == "code":
            lang = v.get("language", "")
            out.append(f"{pad}```{lang}")
            out.append(txt)
            out.append(f"{pad}```")
        elif t == "divider":
            out.append(f"{pad}---")
        elif t == "child_page":
            out.append(f"{pad}- [[子ページ]] {v.get('title', '')}  <!-- {b['id']} -->")
        elif t == "child_database":
            out.append(f"{pad}- [[子DB]] {v.get('title', '')}  <!-- {b['id']} -->")
        elif t == "table_row":
            cells = [rich_text(c) for c in v.get("cells", [])]
            out.append(f"{pad}| " + " | ".join(cells) + " |")
        elif t == "image":
            src = (v.get("external") or v.get("file") or {}).get("url", "")
            out.append(f"{pad}![画像]({src})")
        elif t == "bookmark":
            out.append(f"{pad}[{v.get('url', '')}]({v.get('url', '')})")
        elif t == "equation":
            out.append(f"{pad}$$ {v.get('expression', '')} $$")
        elif t in ("column_list", "column", "synced_block", "table"):
            pass  # 構造だけのブロック。子だけ辿る
        elif txt:
            out.append(f"{pad}{txt}")

        if b.get("has_children") and t != "child_page":
            blocks_to_md(api, b["id"], depth + (0 if t in ("column_list", "column", "synced_block", "table") else 1), out)
    return out


def database_to_md(api, db):
    lines = []
    schema = list((db.get("properties") or {}).keys())
    rows = list(api.paged(f"/databases/{db['id']}/query", {"page_size": 100}))
    if not rows:
        return ["(空のデータベース)"]
    cols = [c for c in schema if c]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        props = r.get("properties") or {}
        cells = [prop_to_text(props.get(c, {})).replace("\n", " ").replace("|", "\\|") for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


# ---------------------------------------------------------------- export


def safe_name(s, maxlen=60):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", s).strip().strip(".")
    return (s[:maxlen] or "untitled")


def build_tree(objects):
    by_id = {o["id"]: o for o in objects}
    children = {}
    roots = []
    for o in objects:
        p = o.get("parent") or {}
        pid = p.get("page_id") or p.get("database_id") or p.get("block_id")
        if pid and pid in by_id:
            children.setdefault(pid, []).append(o["id"])
        else:
            roots.append(o["id"])
    return by_id, children, roots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--full", action="store_true", help="差分を無視して全件取り直す")
    ap.add_argument("--list", action="store_true", help="取得せず一覧のみ")
    args = ap.parse_args()

    api = Notion(load_token())

    me = api.get("/users/me")
    ws = (me.get("bot") or {}).get("owner", {})
    print(f"integration : {me.get('name')} ({me.get('id')})")
    print(f"workspace   : {(me.get('bot') or {}).get('workspace_name')}")

    print("読み取り可能オブジェクトを列挙中...")
    objects = list(api.paged("/search", {"page_size": 100}))
    pages = [o for o in objects if o["object"] == "page"]
    dbs = [o for o in objects if o["object"] == "database"]
    print(f"  ページ {len(pages)} / データベース {len(dbs)} / 合計 {len(objects)}")

    by_id, children, roots = build_tree(objects)

    if args.list:
        def show(oid, d=0):
            o = by_id[oid]
            kind = "DB " if o["object"] == "database" else "page"
            print(f"{'  ' * d}{kind} {title_of(o)[:60]}  [{oid.replace('-', '')}]")
            for c in sorted(children.get(oid, []), key=lambda i: title_of(by_id[i])):
                show(c, d + 1)
        for r in roots:
            show(r)
        return

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "_manifest.json"
    manifest = {}
    if manifest_path.exists() and not args.full:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    new_manifest = {}
    written = skipped = 0

    def path_for(oid):
        parts = []
        cur = oid
        seen = set()
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            parts.append(safe_name(title_of(by_id[cur])))
            p = by_id[cur].get("parent") or {}
            cur = p.get("page_id") or p.get("database_id") or p.get("block_id")
        parts.reverse()
        return out_root.joinpath(*parts[:-1]) / f"{parts[-1]}__{oid.replace('-', '')}.md"

    total = len(objects)
    for i, o in enumerate(objects, 1):
        oid = o["id"]
        edited = o.get("last_edited_time", "")
        dest = path_for(oid)
        new_manifest[oid] = {
            "title": title_of(o),
            "type": o["object"],
            "last_edited_time": edited,
            "url": o.get("url", ""),
            "path": str(dest.relative_to(out_root)),
        }
        prev = manifest.get(oid)
        if prev and prev.get("last_edited_time") == edited and (out_root / prev.get("path", "")).exists():
            if (out_root / prev["path"]) != dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                (out_root / prev["path"]).rename(dest)
            skipped += 1
            continue

        print(f"  [{i}/{total}] {title_of(o)[:50]}")
        header = [
            f"# {title_of(o)}",
            "",
            f"- notion_id: `{oid.replace('-', '')}`",
            f"- type: {o['object']}",
            f"- url: {o.get('url', '')}",
            f"- last_edited: {edited}",
            "",
            "---",
            "",
        ]
        try:
            body = database_to_md(api, o) if o["object"] == "database" else blocks_to_md(api, oid)
        except SystemExit as e:
            print(f"      skip: {e}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(header + body) + "\n", encoding="utf-8")
        written += 1

    manifest_path.write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"出力先   : {out_root}")
    print(f"書き出し : {written} 件 / 変更なし {skipped} 件")
    print(f"API 呼出 : {api.calls} 回")


if __name__ == "__main__":
    main()
