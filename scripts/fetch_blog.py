#!/usr/bin/env python3
"""日向坂46公式ブログから片山紗希さんの記事一覧を取得し、data/saki-blog.json に追記する。

標準ライブラリのみ。GitHub Actions から定期実行する想定。
保存するのは「タイトル・日付・URL・冒頭の短い抜粋」だけで、本文全文や画像は保存しない
（読むときは公式サイトへリンクで飛ぶ）。

環境変数:
  MEMBER_NAME  対象メンバー名（既定: 片山紗希）
  MEMBER_CT    公式サイトのメンバー番号(ct)。未指定ならメンバー一覧から自動で探す
  PAGES        何ページ分さかのぼるか（既定: 1。初回は 10 などで過去分を取り込む）
  OUT          出力先（既定: data/saki-blog.json）
  FIXTURE      テスト用: ネットに出ずこのHTMLファイルを一覧ページとして読む
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

BASE = "https://www.hinatazaka46.com"
LIST_URL = BASE + "/s/official/diary/member/list?ima=0000&ct={ct}&page={page}"
ARTIST_URL = BASE + "/s/official/search/artist?ima=0000"
UA = "Mozilla/5.0 (compatible; saki-blog-aggregator/1.0; personal fan use)"
EXCERPT_LEN = 120


def norm(s):
    return re.sub(r"[\s　]+", "", s or "")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


class BlogListParser(HTMLParser):
    """一覧ページの .p-blog-article ごとに タイトル/日付/名前/本文/リンク を拾う。"""

    FIELDS = {
        "c-blog-article__title": "title",
        "c-blog-article__date": "date",
        "c-blog-article__name": "name",
        "c-blog-article__text": "text",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles = []
        self.cur = None
        self.depth = 0          # 記事 div 内の div の深さ
        self.field = None
        self.field_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "div" and "p-blog-article" in cls and self.cur is None:
            self.cur = {"title": "", "date": "", "name": "", "text": "", "url": ""}
            self.depth = 0
        if self.cur is None:
            return
        if tag == "div":
            self.depth += 1
            for c in cls:
                if c in self.FIELDS and self.field is None:
                    self.field = self.FIELDS[c]
                    self.field_depth = self.depth
        if tag == "a" and not self.cur["url"]:
            m = re.search(r"/s/official/diary/detail/(\d+)", a.get("href") or "")
            if m:
                self.cur["url"] = BASE + "/s/official/diary/detail/" + m.group(1) + "?ima=0000&cd=member"
                self.cur["id"] = m.group(1)
        if tag in ("br", "p") and self.field == "text":
            self.cur["text"] += "\n"

    def handle_endtag(self, tag):
        if self.cur is None or tag != "div":
            return
        if self.field and self.depth == self.field_depth:
            self.field = None
        self.depth -= 1
        if self.depth == 0:
            self.articles.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None and self.field:
            self.cur[self.field] += data


def parse_list(page_html):
    p = BlogListParser()
    p.feed(page_html)
    out = []
    for a in p.articles:
        if not a.get("id"):
            continue
        text = re.sub(r"[ \t　]+", " ", a["text"])
        text = re.sub(r"\s*\n\s*", "\n", text).strip()
        flat = text.replace("\n", " ")
        out.append({
            "id": a["id"],
            "title": a["title"].strip(),
            "date": normalize_date(a["date"]),
            "name": norm(a["name"]),
            "url": a["url"],
            "excerpt": flat[:EXCERPT_LEN] + ("…" if len(flat) > EXCERPT_LEN else ""),
            "chars": len(flat),
        })
    return out


def normalize_date(s):
    """'2025.3.15 21:30' → '2025-03-15T21:30'"""
    m = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s or "")
    if not m:
        return (s or "").strip()
    y, mo, d, h, mi = m.groups()
    out = "%s-%02d-%02d" % (y, int(mo), int(d))
    if h:
        out += "T%02d:%s" % (int(h), mi)
    return out


def find_ct(name):
    """メンバー一覧から名前に一致する artist 番号を探す。"""
    page = fetch(ARTIST_URL)
    target = norm(name)
    for m in re.finditer(r'href="/s/official/artist/(\d+)[^"]*"(.*?)</a>', page, re.S):
        inner = re.sub(r"<[^>]+>", "", m.group(2))
        if target in norm(html.unescape(inner)):
            return m.group(1)
    return None


def main():
    name = os.environ.get("MEMBER_NAME", "片山紗希")
    pages = max(1, int(os.environ.get("PAGES", "1")))
    out_path = os.environ.get("OUT", "data/saki-blog.json")
    fixture = os.environ.get("FIXTURE")

    try:
        with open(out_path, encoding="utf-8") as f:
            db = json.load(f)
    except (FileNotFoundError, ValueError):
        db = {"member": name, "ct": None, "updated": None, "posts": []}

    found = []
    if fixture:
        with open(fixture, encoding="utf-8") as f:
            found = parse_list(f.read())
    else:
        ct = os.environ.get("MEMBER_CT") or db.get("ct") or find_ct(name)
        if not ct:
            sys.exit("メンバー番号(ct)が見つかりません。MEMBER_CT を指定してください。")
        db["ct"] = ct
        for page in range(pages):
            items = parse_list(fetch(LIST_URL.format(ct=ct, page=page)))
            if not items:
                break
            found.extend(items)
            time.sleep(2)  # 公式サイトに負荷をかけない

    target = norm(name)
    mine = [p for p in found if not p["name"] or target in p["name"]]
    if not mine and not db["posts"]:
        sys.exit("記事を1件も読み取れませんでした。公式サイトの構造が変わった可能性があります。")

    by_id = {p["id"]: p for p in db["posts"]}
    added = 0
    for p in mine:
        if p["id"] not in by_id:
            added += 1
        by_id[p["id"]] = p
    db["posts"] = sorted(by_id.values(), key=lambda p: p["date"], reverse=True)
    db["member"] = name
    if added or not db.get("updated"):
        db["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("読み取り %d 件 / 新着 %d 件 / 合計 %d 件" % (len(mine), added, len(db["posts"])))


if __name__ == "__main__":
    main()
