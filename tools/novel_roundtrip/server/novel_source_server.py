#!/usr/bin/env python3
"""网文正文 -> Legado 书源服务（局域网）+ 手机改稿。

只读（Legado 书源）：
  /health                     健康检查
  /search?key=...             搜索书（返回书列表）
  /book/<slug>.json           目录 + 书籍信息
  /c/<slug>/<num>.json        单章正文
  /source/<slug>.json         单本书的 Legado 书源 JSON
  /sources.json               所有书源（一次性导入用）

改稿（需 NOVEL_EDIT_TOKEN）：
  /edit/<slug>                手机浏览器改稿页
  /edit/<slug>/<num>          直接打开某章
  /changelog/<slug>           历史改动列表（HTML）
  /changelog/<slug>.json      历史改动列表（JSON）
  GET  /api/<slug>/<num>      取某章原始 md
  POST /api/<slug>/<num>      保存某章（body: {"token":..,"content":".."}）
  GET  /raw/<slug>/<ts>.diff  某次改动的 unified diff

每次保存：写回正文、生成 .changelog/<ts>-<章号>.diff、git 提交。
"""

import argparse
import base64
import difflib
import hmac
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

BOOKS_ROOT = Path(os.environ.get("NOVEL_BOOKS_ROOT", "~/books")).expanduser()
EDIT_TOKEN = os.environ.get("NOVEL_EDIT_TOKEN", "")
GATEWAY_AUTH_FILE = Path(
    os.environ.get("NOVEL_GATEWAY_AUTH_FILE", "~/.config/novel/gateway-auth")
).expanduser()
CHANGELOG_DIR = ".changelog"
PUBLIC_HOST = os.environ.get("NOVEL_PUBLIC_HOST", "").strip()
# 改稿历史根目录；不设置时沿用 ~/.local/state/novel-roundtrip（测试可整体重定向）。
STATE_ROOT = os.environ.get("NOVEL_STATE_ROOT") or None

# 改稿接口与书源共用端口；增量写入和历史由独立模块处理。
sys.path.insert(0, os.environ.get('NOVEL_ROUNDTRIP_LIB', str(Path(__file__).resolve().parent.parent)))
from manuscript_api import handle as handle_manuscript

CHAP_RE = re.compile(r"^第\s*(\d+)\s*章")
INTRO_HINT = re.compile(r"^(#\s*)?(内容简介|简介|作品简介|大纲)")


def slug_of(project_dir: Path) -> str:
    return project_dir.name


def read_meta(project_dir: Path) -> dict:
    info = {}
    state = project_dir / ".webnovel" / "state.json"
    if state.is_file():
        try:
            info = json.loads(state.read_text(encoding="utf-8")).get("project_info", {})
        except Exception:
            pass
    override = project_dir / ".webnovel" / "title.txt"
    title = info.get("title") or project_dir.name
    if override.is_file():
        text = override.read_text(encoding="utf-8").strip()
        if text:
            title = text
    return {
        "slug": slug_of(project_dir),
        "title": title,
        "author": info.get("author") or "",
        "genre": info.get("genre_label") or info.get("genre") or "",
        "intro": (info.get("core_selling_points") or info.get("world_scale") or "").strip(),
        "dir": project_dir,
    }


def list_books() -> list:
    out = []
    if not BOOKS_ROOT.is_dir():
        return out
    for project_dir in sorted(BOOKS_ROOT.iterdir()):
        if not (project_dir / "正文").is_dir():
            continue
        out.append(read_meta(project_dir))
    return out


def find_book(slug: str):
    for book in list_books():
        if book["slug"] == slug:
            return book
    return None


def chapters_of(book: dict) -> list:
    """返回 [{num, title, path}]，按章节号排序。"""
    items = []
    for path in (book["dir"] / "正文").glob("*.md"):
        m = CHAP_RE.search(path.stem)
        num = int(m.group(1)) if m else None
        if num is None:
            nums = re.findall(r"\d+", path.stem)
            num = int(nums[0]) if nums else 0
        items.append({"num": num, "title": path.stem, "path": path})
    items.sort(key=lambda x: x["num"])
    return items


def clean_text(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if not s:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        lines.append(s.replace("　　", "　　"))
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def source_headers() -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    raw = ""
    if GATEWAY_AUTH_FILE.is_file():
        raw = GATEWAY_AUTH_FILE.read_text(encoding="utf-8").strip()
    raw = raw or os.environ.get("NOVEL_GATEWAY_AUTH", "")
    if raw:
        token = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return json.dumps(headers, ensure_ascii=False)


def build_book_source(book: dict, host: str, proto: str = "http") -> dict:
    base = f"{proto}://{host}"
    return {
        "bookSourceGroup": "我的网文",
        "bookSourceName": f"{book['title']}（本机）",
        "bookSourceType": 0,
        "bookSourceUrl": f"{base}/",
        "bookSourceComment": "由本机 novel-source-server 提供，正文来自 ~/books。局域网直连。",
        "bookUrlPattern": f"{base}/book/{book['slug']}.*",
        "loginUrl": "",
        "header": source_headers(),
        "enabled": True,
        "enabledExplore": False,
        "enabledFind": False,
        "exploreUrl": f"{base}/search?key=",
        "searchUrl": f"{base}/search?key={{{{key}}}}",
        "ruleExplore": {
            "bookList": "$.results[*]",
            "name": "$.title",
            "author": "$.author",
            "kind": "$.kind",
            "intro": "$.intro",
            "lastChapter": "$.lastChapter",
            "bookUrl": "$.bookUrl",
        },
        "ruleSearch": {
            "bookList": "$.results[*]",
            "name": "$.title",
            "author": "$.author",
            "kind": "$.kind",
            "intro": "$.intro",
            "lastChapter": "$.lastChapter",
            "bookUrl": "$.bookUrl",
        },
        "ruleBookInfo": {
            "name": "$.name",
            "author": "$.author",
            "intro": "$.intro",
            "kind": "$.kind",
            "lastChapter": "$.lastChapter",
            "wordCount": "$.wordCount",
        },
        "ruleToc": {
            "chapterList": "$.chapters[*]",
            "chapterName": "$.title",
            "chapterUrl": "$.url",
        },
        "ruleContent": {
            "content": "$.content",
            "nextContentUrl": "",
        },
    }


def book_payload(book: dict, host: str, proto: str = "http") -> dict:
    base = f"{proto}://{host}"
    chapters = []
    total_words = 0
    items = chapters_of(book)
    for item in items:
        raw = item["path"].read_text(encoding="utf-8", errors="replace")
        body = clean_text(raw)
        total_words += len(body)
        title = item["title"]
        head = raw.strip().splitlines()[0] if raw.strip() else ""
        if head.startswith("#"):
            title = head.lstrip("# ").strip()
        chapters.append(
            {
                "title": title,
                "num": item["num"],
                "url": f"{base}/c/{book['slug']}/{item['num']}.json?v={hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}",
                "words": len(body),
            }
        )
    return {
        "name": book["title"],
        "author": book["author"],
        "kind": book["genre"],
        "intro": book["intro"],
        "slug": book["slug"],
        "chapterCount": len(chapters),
        "wordCount": total_words,
        "lastChapter": chapters[-1]["title"] if chapters else "",
        "updateTime": str(datetime_now()),
        "chapters": chapters,
    }


def datetime_now() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def chapter_payload(slug: str, num: int) -> dict | None:
    book = find_book(slug)
    if not book:
        return None
    for item in chapters_of(book):
        if item["num"] == num:
            raw = item["path"].read_text(encoding="utf-8", errors="replace")
            title = item["title"]
            head = raw.strip().splitlines()[0] if raw.strip() else ""
            if head.startswith("#"):
                title = head.lstrip("# ").strip()
            return {"title": title, "num": num, "content": clean_text(raw)}
    return None


def changelog_dir(book: dict) -> Path:
    return book["dir"] / CHANGELOG_DIR


def _normalize(new_raw: str, old_raw: str) -> str:
    """保留首行标题行：手机上删掉 # 标题时按旧标题补回。"""
    text = new_raw.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    head = old_raw.strip().splitlines()[0] if old_raw.strip() else ""
    if head.startswith("#"):
        first = text.split("\n")[0].strip()
        if not first.startswith("#"):
            text = head + "\n" + text
    return text


def _git(project: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return (out.stdout or out.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"git failed: {exc}"


def save_chapter(book: dict, item: dict, content: str, remote: str = "") -> dict:
    path: Path = item["path"]
    rel = path.relative_to(book["dir"])
    old_raw = path.read_text(encoding="utf-8", errors="replace")
    new_raw = _normalize(content, old_raw)
    if new_raw == _normalize(old_raw, old_raw):
        return {"changed": False, "message": "内容无变化", "added": 0, "removed": 0}
    diff_lines = list(
        difflib.unified_diff(
            old_raw.splitlines(),
            new_raw.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
            n=2,
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(
        1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    cl = changelog_dir(book)
    cl.mkdir(exist_ok=True)
    diff_name = f"{stamp}-第{item['num']:04d}章.diff"
    diff_text = "\n".join(diff_lines) + "\n"
    (cl / diff_name).write_text(diff_text, encoding="utf-8")
    path.write_text(new_raw, encoding="utf-8")

    commit = _git(book["dir"], "add", "--", str(rel))
    who = f"（{remote}）" if remote else ""
    _git(
        book["dir"],
        "commit",
        "-m",
        f"edit: 第{item['num']}章 手机改稿 +{added} -{removed}{who}",
    )
    log_line = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\t第{item['num']}章\t"
        f"+{added}\t-{removed}\t{diff_name}\t{remote}\n"
    )
    with (cl / "log.tsv").open("a", encoding="utf-8") as fh:
        fh.write(log_line)
    return {
        "changed": True,
        "added": added,
        "removed": removed,
        "diff": diff_text,
        "diffName": diff_name,
        "url": f"/raw/{book['slug']}/{quote(diff_name)}",
        "commit": commit,
        "message": f"已保存：+{added} -{removed}",
    }


def changelog_index(book: dict) -> list:
    cl = changelog_dir(book)
    if not cl.is_dir():
        return []
    out = []
    for path in sorted(cl.glob("*.diff"), reverse=True):
        head = path.read_text(encoding="utf-8", errors="replace").splitlines()
        added = sum(1 for line in head if line.startswith("+") and not line.startswith("+++"))
        removed = sum(
            1 for line in head if line.startswith("-") and not line.startswith("---")
        )
        m = re.search(r"第(\d+)章", path.stem)
        out.append(
            {
                "name": path.name,
                "chapter": int(m.group(1)) if m else None,
                "added": added,
                "removed": removed,
                "url": f"/raw/{book['slug']}/{quote(path.name)}",
            }
        )
    return out


CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#14161a; color:#e6e6e6;
  font: 17px/1.75 -apple-system,"Noto Sans CJK SC","PingFang SC",sans-serif; }
header { position: sticky; top:0; z-index:5; background:#1d2026; padding:10px 12px;
  border-bottom:1px solid #2c313a; }
h1 { font-size:16px; margin:0 0 6px; }
select, input, button { font-size:16px; padding:9px 10px; border-radius:8px;
  border:1px solid #39414d; background:#262b33; color:#e6e6e6; width:100%; }
button { background:#2f6fdd; border-color:#2f6fdd; font-weight:600; }
button.ghost { background:#262b33; border-color:#39414d; }
.row { display:flex; gap:8px; margin-top:8px; }
.row > * { flex:1; }
textarea { width:100%; min-height:56vh; margin-top:10px; padding:12px;
  background:#101216; color:#dfe3ea; border:1px solid #2c313a; border-radius:8px;
  font: 18px/1.9 "Noto Serif CJK SC",serif; resize:vertical; }
pre { white-space:pre-wrap; word-break:break-word; background:#101216;
  border:1px solid #2c313a; border-radius:8px; padding:10px; font-size:14px;
  overflow:auto; max-height:50vh; }
.add { color:#6fbf73; } .del { color:#e06c75; } .meta { color:#8b93a1; }
.bar { font-size:13px; color:#8b93a1; padding:6px 12px; }
a { color:#6aa6ff; }
ul { list-style:none; padding:0 12px; } li { border-bottom:1px solid #262b33; }
li a { display:block; padding:11px 2px; text-decoration:none; }
"""

EDIT_JS = """
const S = { book: BOOK_JSON, chapters: CHAPTERS_JSON, host: HOST };
let token = localStorage.getItem('novel_token')
  || new URLSearchParams(location.search).get('token') || '';
if(token) localStorage.setItem('novel_token', token);
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function api(p, opts){ return fetch(p, opts); }
async function load(num){
  const r = await api('/api/'+S.book.slug+'/'+num);
  if(!r.ok){ alert('读取失败'); return; }
  const d = await r.json();
  document.getElementById('text').value = d.content;
  document.getElementById('meta').textContent = d.file;
  document.getElementById('diff').innerHTML = '';
}
async function save(){
  const num = document.getElementById('chap').value;
  const btn = document.getElementById('save');
  btn.disabled = true; btn.textContent = '保存中…';
  try{
    const r = await api('/api/'+S.book.slug+'/'+num, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ token: token, content: document.getElementById('text').value })
    });
    const d = await r.json();
    if(!r.ok || d.error){ alert('保存失败：'+(d.error||r.status)); return; }
    if(!d.changed){ alert('内容无变化'); return; }
    const html = d.diff.split('\\n').map(l => {
      const c = l.startsWith('+++')||l.startsWith('---')||l.startsWith('@@') ? 'meta'
        : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : '';
      return '<span class="'+c+'">'+(esc(l)||' ')+'</span>';
    }).join('\\n');
    document.getElementById('diff').innerHTML =
      '<div class="bar">已保存 +'+d.added+' -'+d.removed+'　'
      + '<a href="'+d.url+'" target="_blank">原始 diff</a></div><pre>'+html+'</pre>';
  } finally { btn.disabled = false; btn.textContent = '保存'; }
}
function bind(){
  document.getElementById('token').value = token;
  document.getElementById('save').onclick = save;
  document.getElementById('token').onchange = e => {
    token = e.target.value.trim(); localStorage.setItem('novel_token', token);
  };
  document.getElementById('chap').onchange = e => load(e.target.value);
  load(document.getElementById('chap').value);
}
"""


def edit_page(book: dict, host: str, num: int | None) -> str:
    items = chapters_of(book)
    if not items:
        return "<h1>这本书还没有正文</h1>"
    if num is None:
        num = items[-1]["num"]
    options = "".join(
        f'<option value="{i["num"]}"{" selected" if i["num"] == num else ""}>'
        f'{i["title"]}</option>'
        for i in items
    )
    warn = "" if EDIT_TOKEN else (
        "<div class='bar' style='color:#e06c75'>服务端未配置 NOVEL_EDIT_TOKEN，写入已禁用</div>"
    )
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>改稿 · {book['title']}</title><style>{CSS}</style></head><body>
<header><h1>改稿 · {book['title']}</h1>{warn}
<select id="chap">{options}</select>
<div class="row"><input id="token" type="password" placeholder="编辑口令 token"></div>
<div class="row"><button id="save">保存</button>
<a href="/changelog/{book['slug']}" target="_blank"><button class="ghost">改动历史</button></a></div>
</header>
<div class="bar" id="meta"></div>
<textarea id="text" spellcheck="false"></textarea>
<div id="diff"></div>
<script>
const BOOK_JSON={json.dumps(book['slug'])};
const CHAPTERS_JSON={json.dumps([i['num'] for i in items])};
const HOST={json.dumps(host)};
{EDIT_JS}
bind();
</script></body></html>"""


def changelog_page(book: dict, host: str) -> str:
    rows = changelog_index(book)
    if not rows:
        body = "<div class='bar'>还没有改动记录</div>"
    else:
        items = []
        for r in rows:
            chap = f"第{r['chapter']}章" if r["chapter"] is not None else "?"
            items.append(
                f"<li><a href='{r['url']}'>{chap}　<span class='add'>+{r['added']}</span> "
                f"<span class='del'>-{r['removed']}</span>　"
                f"<span class='meta'>{r['name']}</span></a></li>"
            )
        body = "<ul>" + "".join(items) + "</ul>"
    return (
        f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>改动历史 · {book['title']}</title><style>{CSS}</style></head><body>"
        f"<header><h1>改动历史 · {book['title']}</h1>"
        f"<a href='/edit/{book['slug']}'><button class='ghost'>返回改稿</button></a>"
        f"</header>{body}</body></html>"
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "NovelSource/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _token_ok(self, given: str) -> bool:
        return bool(EDIT_TOKEN) and hmac.compare_digest(str(given or ""), EDIT_TOKEN)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        if path.startswith("/novel/"):
            path = path[len("/novel"):]
        elif path == "/novel":
            path = "/"
        host = self.headers.get("Host") or f"127.0.0.1:{PORT}"
        prefix = (self.headers.get("X-Forwarded-Prefix") or "").rstrip("/")
        proto = self.headers.get("X-Forwarded-Proto")
        if not proto:
            proto = "https" if PUBLIC_HOST and PUBLIC_HOST in host else "http"
        if not prefix and PUBLIC_HOST and PUBLIC_HOST in host:
            prefix = "/novel"
        if prefix:
            host = f"{host}{prefix}"
        try:
            self._route(path, parse_qs(parsed.query), host, proto)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._send({"error": str(exc)}, code=500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path).rstrip("/") or "/"
        if path.startswith("/novel/"):
            path = path[len("/novel"):]
        elif path == "/novel":
            path = "/"
        try:
            if handle_manuscript(self, path, BOOKS_ROOT, EDIT_TOKEN,
                                 json.loads(source_headers()).get('Authorization', ''),
                                 state_root=STATE_ROOT):
                return
            self._route_post(path, self._read_body())
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._send({"error": str(exc)}, code=500)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_HEAD(self):
        parsed = urlparse(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _route_post(self, path: str, body: dict):
        if not path.startswith("/api/"):
            return self._send({"error": "not found", "path": path}, code=404)
        parts = path[len("/api/") :].split("/")
        if len(parts) != 2 or not parts[1].isdigit():
            return self._send({"error": "bad request"}, code=400)
        if not self._token_ok(body.get("token")):
            return self._send({"error": "token 无效"}, code=403)
        book = find_book(parts[0])
        if not book:
            return self._send({"error": "not found"}, code=404)
        num = int(parts[1])
        target = None
        for item in chapters_of(book):
            if item["num"] == num:
                target = item
                break
        if not target:
            return self._send({"error": "章节不存在"}, code=404)
        content = str(body.get("content") or "")
        if not content.strip():
            return self._send({"error": "内容为空"}, code=400)
        record = save_chapter(book, target, content, remote=self.client_address[0])
        return self._send({"ok": True, **record})

    def _route(self, path: str, query: dict, host: str, proto: str = "http"):
        if handle_manuscript(self, path, BOOKS_ROOT, EDIT_TOKEN,
                             json.loads(source_headers()).get('Authorization', ''),
                             state_root=STATE_ROOT):
            return
        if path == "/health":
            return self._send({"ok": True, "books": len(list_books())})
        if path == "/search":
            key = (query.get("key") or [""])[0].strip()
            results = []
            for book in list_books():
                hay = f"{book['title']} {book['author']} {book['genre']} {book['slug']}".lower()
                if key and unicodedata.normalize("NFKC", key).lower() not in hay:
                    continue
                payload = book_payload(book, host, proto)
                results.append(
                    {
                        "title": book["title"],
                        "author": book["author"],
                        "kind": book["genre"],
                        "intro": book["intro"],
                        "lastChapter": payload["lastChapter"],
                        "bookUrl": f"{proto}://{host}/book/{book['slug']}.json",
                    }
                )
            return self._send({"results": results})
        if path == "/sources.json":
            return self._send([build_book_source(b, host, proto) for b in list_books()])
        if path.startswith("/source/"):
            slug = path[len("/source/") :].removesuffix(".json")
            book = find_book(slug)
            if not book:
                return self._send({"error": "not found"}, code=404)
            return self._send([build_book_source(book, host, proto)])
        if path.startswith("/source-one/"):
            slug = path[len("/source-one/") :].removesuffix(".json")
            book = find_book(slug)
            if not book:
                return self._send({"error": "not found"}, code=404)
            return self._send(build_book_source(book, host, proto))
        if path.startswith("/book/"):
            slug = path[len("/book/") :].removesuffix(".json")
            book = find_book(slug)
            if not book:
                return self._send({"error": "not found"}, code=404)
            return self._send(book_payload(book, host, proto))
        if path.startswith("/c/"):
            parts = path[len("/c/") :].split("/")
            if len(parts) == 2:
                slug = parts[0]
                num = parts[1].removesuffix(".json")
                if not num.isdigit():
                    return self._send({"error": "bad chapter"}, code=400)
                payload = chapter_payload(slug, int(num))
                if not payload:
                    return self._send({"error": "not found"}, code=404)
                return self._send(payload)
        if path.startswith("/edit/"):
            slug = path[len("/edit/") :]
            book = find_book(slug)
            if not book:
                return self._send_html("<h1>书不存在</h1>", code=404)
            num = None
            if "/" in slug:
                slug, tail = slug.split("/", 1)
                if not tail.isdigit():
                    return self._send_html("<h1>章节号无效</h1>", code=400)
                book = find_book(slug)
                num = int(tail)
            return self._send_html(edit_page(book, host, num))
        if path.startswith("/changelog/"):
            slug = path[len("/changelog/") :]
            if slug.endswith(".json"):
                slug = slug[: -len(".json")]
                book = find_book(slug)
                if not book:
                    return self._send({"error": "not found"}, code=404)
                return self._send({"entries": changelog_index(book)})
            book = find_book(slug)
            if not book:
                return self._send_html("<h1>书不存在</h1>", code=404)
            return self._send_html(changelog_page(book, host))
        if path.startswith("/api/"):
            parts = path[len("/api/") :].split("/")
            if len(parts) == 2 and parts[1].isdigit():
                book = find_book(parts[0])
                if not book:
                    return self._send({"error": "not found"}, code=404)
                for item in chapters_of(book):
                    if item["num"] == int(parts[1]):
                        return self._send(
                            {
                                "slug": book["slug"],
                                "num": item["num"],
                                "file": item["title"],
                                "content": item["path"].read_text(
                                    encoding="utf-8", errors="replace"
                                ),
                            }
                        )
                return self._send({"error": "章节不存在"}, code=404)
            return self._send({"error": "bad request"}, code=400)
        if path.startswith("/raw/"):
            rest = path[len("/raw/") :]
            if "/" not in rest:
                return self._send({"error": "bad request"}, code=400)
            slug, name = rest.split("/", 1)
            book = find_book(slug)
            if not book:
                return self._send({"error": "not found"}, code=404)
            target = changelog_dir(book) / name
            if not target.is_file():
                return self._send({"error": "not found"}, code=404)
            body = target.read_text(encoding="utf-8", errors="replace")
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return None
        return self._send({"error": "not found", "path": path}, code=404)


PORT = 8790

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--books-root", default=str(BOOKS_ROOT))
    args = ap.parse_args()
    BOOKS_ROOT = Path(args.books_root).expanduser()
    PORT = args.port
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"novel-source-server on http://{args.host}:{args.port} books={BOOKS_ROOT}", flush=True)
    srv.serve_forever()
