#!/usr/bin/env python3
"""研究雷达数据抓取: arXiv API + RSS(含 RDF/Atom 容错) + HN Algolia → data/radar/raw/YYYY-MM-DD/items.json

纯 stdlib (python3.9), 无第三方依赖。每个源独立 try/except, 单源失败不影响其他源;
输出源健康报告(抓取数/错误), 供后续告警判断。
用法: python3 radar_fetch.py [YYYY-MM-DD]
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

BASE = os.environ.get("DAILY_BASE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR = os.path.join(BASE, "data", "radar")
RAW = os.path.join(RADAR, "raw")
CONFIG = json.load(open(os.path.join(RADAR, "config.json"), encoding="utf-8"))
UA = {"User-Agent": "Mozilla/5.0 (compatible; daily-radar/1.0)"}
TRUNC = CONFIG.get("textTruncate", 1200)


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_iso(s):
    """ISO8601 → UTC isoformat 或 None。"""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def parse_rfc2822(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        return dt.astimezone(timezone.utc).isoformat() if dt else None
    except Exception:
        return None


def local(tag):
    return tag.split("}")[-1]


def strip_html(s):
    return re.sub(r"<[^>]+>", " ", s or "")


def clean_text(s):
    return re.sub(r"\s+", " ", strip_html(s)).strip()


def truncate(s):
    s = clean_text(s)
    return s[:TRUNC] if len(s) > TRUNC else s


def arxiv_id(eid):
    """entry.id → 短 id (abs/XXXX.XXXXX)。"""
    m = re.search(r"(abs/[^/]+)$", eid)
    return m.group(1) if m else eid


def fetch_arxiv(cats, lookback_hours, max_results):
    """submittedDate 窗口查询 + 分页(start 每 100), 请求间隔 >=3s。"""
    items = []
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    since_s = since.strftime("%Y%m%d%H%M")
    for cat in cats:
        start = 0
        while start < max_results:
            q = urllib.parse.quote(f"cat:{cat} AND submittedDate:[{since_s} TO 999912312359]")
            url = (f"https://export.arxiv.org/api/query?search_query={q}"
                   f"&sortBy=submittedDate&sortOrder=descending&start={start}&max_results=100")
            try:
                xml = http_get(url)
            except Exception as e:
                print(f"  ⚠️ arxiv/{cat} start={start} 失败: {e}")
                raise
            ns = {"a": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(xml)
            entries = root.findall("a:entry", ns)
            for e in entries:
                title = clean_text(e.findtext("a:title", "", ns))
                summ = truncate(e.findtext("a:summary", "", ns))
                if not title:
                    continue
                authors = [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)]
                items.append({
                    "id": arxiv_id(e.findtext("a:id", "", ns).strip()),
                    "source": "arxiv",
                    "sourceUrl": e.findtext("a:id", "", ns).strip(),
                    "title": title,
                    "author": ", ".join([a for a in authors if a]) or None,
                    "publishedAt": parse_iso(e.findtext("a:published", "", ns)),
                    "text": summ,
                })
            print(f"  arxiv/{cat} start={start}: +{len(entries)} 条")
            if len(entries) < 100:
                break
            start += 100
            time.sleep(3)
    return items


def rss_find(el, names):
    """按 local-name 容错查找: RSS2 与 RDF/RSS1 命名空间通吃。"""
    for child in el.iter():
        if local(child.tag) in names and child.text:
            return child.text
    return None


def rss_children(el, name):
    return [c for c in el.iter() if local(c.tag) == name]


def fetch_rss(url):
    """RSS 2.0 / RSS 1.0(RDF) / Atom 三合一容错解析。"""
    xml = http_get(url)
    root = ET.fromstring(xml)
    items = []
    if local(root.tag) == "feed":  # Atom
        for e in root:
            if local(e.tag) != "entry":
                continue
            title = clean_text(rss_find(e, {"title"}))
            link = None
            for c in e:
                if local(c.tag) == "link":
                    href = c.get("href") or c.text
                    if href:
                        link = href.strip()
                        break
            if not title or not link:
                continue
            items.append({
                "id": link, "source": "rss", "sourceUrl": link, "title": title,
                "author": None,
                "publishedAt": parse_iso(rss_find(e, {"published", "updated"}))
                              or parse_rfc2822(rss_find(e, {"published", "updated"})),
                "text": truncate(rss_find(e, {"summary", "content", "description"})),
            })
    else:  # RSS 2.0 / RDF
        for e in rss_children(root, "item"):
            title = clean_text(rss_find(e, {"title"}))
            link = clean_text(rss_find(e, {"link"}))
            if not title or not link:
                continue
            desc = rss_find(e, {"encoded", "description"})  # content:encoded 优先
            items.append({
                "id": link, "source": "rss", "sourceUrl": link, "title": title,
                "author": clean_text(rss_find(e, {"creator", "author"})) or None,
                "publishedAt": parse_rfc2822(rss_find(e, {"pubDate", "date"}))
                               or parse_iso(rss_find(e, {"date", "pubDate"})),
                "text": truncate(desc),
            })
    return items


def fetch_hn(query):
    """HN Algolia search_by_date, 只看 story, 按时间倒序。"""
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=50&"
           + urllib.parse.urlencode({"query": query}))
    data = json.loads(http_get(url))
    items = []
    for h in data.get("hits", []):
        title = clean_text(h.get("title"))
        if not title:
            continue
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        items.append({
            "id": f"hn-{h.get('objectID')}", "source": "hn", "sourceUrl": link,
            "title": title, "author": h.get("author") or None,
            "publishedAt": parse_iso(h.get("created_at")),
            "text": truncate(h.get("story_text") or h.get("comment_text") or ""),
        })
    return items


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    outdir = os.path.join(RAW, date)
    os.makedirs(outdir, exist_ok=True)

    items, health = [], {}
    srcs = []
    for cat in CONFIG["arxiv"]["categories"]:
        srcs.append(("arxiv", cat, lambda c=cat: fetch_arxiv(
            [c], CONFIG["arxiv"]["lookbackHours"], CONFIG["arxiv"]["maxResults"])))
    for r in CONFIG["rss"]:
        srcs.append(("rss", r["id"], lambda u=r["url"]: fetch_rss(u)))
    for h in CONFIG["hn"]:
        srcs.append(("hn", h["query"], lambda q=h["query"]: fetch_hn(q)))

    for kind, sid, fn in srcs:
        t0 = time.time()
        try:
            got = fn()
            items.extend(got)
            health[f"{kind}:{sid}"] = {"ok": True, "items": len(got), "secs": round(time.time() - t0, 1)}
            print(f"✅ {kind}/{sid}: {len(got)} 条 ({time.time()-t0:.1f}s)")
        except Exception as e:
            health[f"{kind}:{sid}"] = {"ok": False, "error": str(e)[:200], "secs": round(time.time() - t0, 1)}
            print(f"❌ {kind}/{sid}: {e}")

    # 同一天内按 URL 去重(跨源转载)
    seen, uniq = set(), []
    for it in items:
        if it["sourceUrl"] in seen:
            continue
        seen.add(it["sourceUrl"])
        uniq.append(it)
    print(f"抓取 {len(items)} 条 → 去重后 {len(uniq)} 条")

    out = {"date": date, "fetchedAt": datetime.now(timezone.utc).isoformat(),
           "items": uniq, "sourceHealth": health}
    with open(os.path.join(outdir, "items.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"→ {os.path.join(outdir, 'items.json')}")


if __name__ == "__main__":
    main()
