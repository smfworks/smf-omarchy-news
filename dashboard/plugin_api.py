"""SMF Omarchy News — aggregated public Omarchy feed for Hermes Desktop.

Sources (required):

* Official HTML listing ``https://omarchy.org/news/`` (articles under
  ``/news/YYYY/MM/...``). Same-site RSS is a fallback if HTML parse is empty.
* GitHub Releases ``https://api.github.com/repos/basecamp/omarchy/releases``
  (GitHub currently redirects that repo; we follow redirects).

Sources (optional, labeled community):

* Reddit ``r/omarchy`` Atom RSS
* Hacker News via hnrss (query Omarchy)

Never invents headlines or prose. Disk cache under the Hermes home with TTL.
Refresh failure serves cache with ``stale: true`` and age. Total failure with
no cache returns ``ok: false`` and ``errors[]``.

``GET /feed`` query params:

* ``refresh`` — ``1`` / ``true`` bypasses TTL and hits the network.

``GET /article?id=`` or ``GET /article/{id}`` — full body from cache or an
allowlisted fetch of the official article page.
"""
from __future__ import annotations

import html
import json
import os
import re
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter()
except ImportError:  # tests / hosts without FastAPI still import the helpers
    APIRouter = None  # type: ignore[misc, assignment]
    JSONResponse = None  # type: ignore[misc, assignment]
    router = None

PLUGIN = "smf-omarchy-news"
USER_AGENT = "SMF-Omarchy-News/1.0 (+https://github.com/smfworks/smf-omarchy-news)"
FEED_TTL_SECONDS = 15 * 60
ARTICLE_TTL_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT = 12
LEDE_CHARS = 280

OFFICIAL_NEWS_URL = "https://omarchy.org/news/"
OFFICIAL_RSS_URL = "https://omarchy.org/news/rss.xml"
GITHUB_RELEASES_URL = "https://api.github.com/repos/basecamp/omarchy/releases"
REDDIT_RSS_URL = "https://www.reddit.com/r/omarchy/.rss"
HN_RSS_URL = "https://hnrss.org/newest?q=Omarchy"

SOURCE_OFFICIAL = "Omarchy.org"
SOURCE_RELEASES = "Omarchy releases"
SOURCE_REDDIT = "Reddit r/omarchy"
SOURCE_HN = "Hacker News"

ALLOWED_FETCH_HOSTS = frozenset({
    "omarchy.org",
    "www.omarchy.org",
    "api.github.com",
    "github.com",
    "www.reddit.com",
    "old.reddit.com",
    "hnrss.org",
    "news.ycombinator.com",
})

_NEWS_HREF_RE = re.compile(r"^/news/(\d{4})/(\d{2})/([a-z0-9\-]+)/?$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)

_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

HttpGetter = Callable[[str, Optional[Dict[str, str]]], Tuple[int, str, Dict[str, str]]]


# ---------------------------------------------------------------------------
# Time / text
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> Optional[str]:
    """Return an ISO-8601 UTC timestamp, or None. Never guesses a date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        iso = text[:-1] + "+00:00"
    else:
        iso = text
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def strip_tags(value: str) -> str:
    if not value:
        return ""
    unescaped = html.unescape(value)
    no_script = _SCRIPT_RE.sub(" ", unescaped)
    no_style = _STYLE_RE.sub(" ", no_script)
    return collapse_ws(_TAG_RE.sub(" ", no_style))


def lede_from_text(text: Optional[str], limit: int = LEDE_CHARS) -> Optional[str]:
    if not text:
        return None
    cleaned = collapse_ws(strip_tags(text))
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def slug_id(kind: str, key: str) -> str:
    raw = collapse_ws(key)
    slug = re.sub(r"[^a-zA-Z0-9._:-]+", "-", raw).strip("-")[:160]
    if not slug:
        slug = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{slug}"


def news_id_from_path(path: str) -> str:
    m = _NEWS_HREF_RE.match(path if path.startswith("/") else "/" + path.strip("/"))
    if not m:
        return slug_id("news", path)
    return slug_id("news", f"{m.group(1)}-{m.group(2)}-{m.group(3)}")


def absolutize(url: Optional[str], base: str = "https://omarchy.org") -> Optional[str]:
    if not url:
        return None
    url = html.unescape(url).strip()
    if not url or url.startswith("data:") or url.startswith("javascript:"):
        return None
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        return urljoin(base, url)
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return url
    return urljoin(base, url)


def host_allowed(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    host = host.lower()
    if host in ALLOWED_FETCH_HOSTS:
        return True
    # Language subdomains of omarchy.org (zh.omarchy.org, etc.)
    return host.endswith(".omarchy.org")


# ---------------------------------------------------------------------------
# HTML sanitizer (keep a small tag set; drop scripts)
# ---------------------------------------------------------------------------

_KEEP_TAGS = frozenset({
    "p", "a", "strong", "em", "b", "i", "ul", "ol", "li", "h2", "h3", "h4",
    "blockquote", "code", "pre", "br", "img", "hr", "span",
})
_VOID = frozenset({"br", "img", "hr"})


class _SanitizeParser(HTMLParser):
    def __init__(self, base: str = "https://omarchy.org") -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button"}:
            self._skip += 1
            return
        if self._skip or tag not in _KEEP_TAGS:
            return
        attr_map = {k.lower(): v for k, v in attrs if k}
        kept: List[str] = []
        if tag == "a":
            href = absolutize(attr_map.get("href"), self.base)
            if href and href.startswith("https://"):
                kept.append(f'href="{html.escape(href, quote=True)}"')
                kept.append('rel="noreferrer noopener"')
                kept.append('target="_blank"')
        if tag == "img":
            src = absolutize(attr_map.get("src"), self.base)
            if not src:
                return
            kept.append(f'src="{html.escape(src, quote=True)}"')
            alt = attr_map.get("alt") or ""
            kept.append(f'alt="{html.escape(alt, quote=True)}"')
        bits = " ".join([tag] + kept)
        if tag in _VOID:
            self.out.append(f"<{bits}>")
        else:
            self.out.append(f"<{bits}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button"}:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag not in _KEEP_TAGS or tag in _VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.out.append(html.escape(data, quote=False))


def sanitize_html(raw: Optional[str], base: str = "https://omarchy.org") -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    parser = _SanitizeParser(base=base)
    try:
        parser.feed(str(raw))
        parser.close()
    except Exception:
        return None
    out = "".join(parser.out).strip()
    return out or None


def markdown_to_html(text: Optional[str]) -> Optional[str]:
    """Small, lossy Markdown subset for GitHub release notes. Does not invent."""
    if not text or not str(text).strip():
        return None
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", raw)
    html_blocks: List[str] = []
    for block in blocks:
        lines = block.split("\n")
        if all(re.match(r"^\s*[-*]\s+", ln) for ln in lines if ln.strip()):
            items = []
            for ln in lines:
                m = re.match(r"^\s*[-*]\s+(.*)$", ln)
                if m:
                    items.append(f"<li>{_inline_md(m.group(1))}</li>")
            html_blocks.append("<ul>" + "".join(items) + "</ul>")
            continue
        first = lines[0]
        hm = re.match(r"^(#{1,4})\s+(.*)$", first)
        if hm:
            level = min(len(hm.group(1)) + 1, 4)  # h2–h4 in the reader
            rest = "\n".join(lines[1:]).strip()
            inner = _inline_md(hm.group(2))
            html_blocks.append(f"<h{level}>{inner}</h{level}>")
            if rest:
                html_blocks.append(f"<p>{_inline_md(rest)}</p>")
            continue
        html_blocks.append(f"<p>{_inline_md(block)}</p>")
    return sanitize_html("".join(html_blocks), base="https://github.com")


def _inline_md(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" rel="noreferrer noopener" target="_blank">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("\n", "<br>")
    return escaped


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

def normalize_item(
    raw: Optional[Dict[str, Any]],
    *,
    fetched_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize a source dict. Missing title → drop (never invent)."""
    if not raw or not isinstance(raw, dict):
        return None
    title = collapse_ws(strip_tags(str(raw.get("title") or "")))
    if not title:
        return None
    source_url = absolutize(raw.get("source_url") or raw.get("url") or raw.get("link"))
    source_name = collapse_ws(str(raw.get("source_name") or raw.get("source") or "")) or None
    if not source_name:
        return None
    body_html = raw.get("body_html")
    if body_html:
        body_html = sanitize_html(str(body_html), base=source_url or "https://omarchy.org")
    else:
        body_html = None
    body_text = raw.get("body_text")
    if body_text:
        body_text = collapse_ws(strip_tags(str(body_text))) or None
    elif body_html:
        body_text = strip_tags(body_html) or None
    lede = raw.get("lede")
    if lede:
        lede = lede_from_text(str(lede))
    else:
        lede = lede_from_text(body_text or body_html)
    image_url = absolutize(raw.get("image_url"), base=source_url or "https://omarchy.org")
    published_at = parse_datetime(raw.get("published_at") or raw.get("pubDate") or raw.get("published"))
    fetched = parse_datetime(raw.get("fetched_at")) or fetched_at or parse_datetime(utcnow())
    item_id = str(raw.get("id") or "").strip()
    if not item_id:
        item_id = slug_id("item", source_url or title)
    return {
        "id": item_id,
        "title": title,
        "lede": lede,
        "body_html": body_html,
        "body_text": body_text,
        "image_url": image_url,
        "source_name": source_name,
        "source_url": source_url,
        "published_at": published_at,
        "fetched_at": fetched,
    }


def sort_items(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Newest first. Missing dates sort last (empty string < any ISO timestamp).
    return sorted(items, key=lambda it: it.get("published_at") or "", reverse=True)


def dedupe_items(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        ident = it.get("id") or it.get("source_url")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------

class _NewsIndexParser(HTMLParser):
    """Extract listing cards: /news/YYYY/MM/slug + time + title + lede."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[Dict[str, Any]] = []
        self._in_a = False
        self._depth = 0
        self._href: Optional[str] = None
        self._datetime: Optional[str] = None
        self._in_time = False
        self._in_svg = 0
        self._texts: List[str] = []
        self._span_texts: List[str] = []
        self._cur_span: List[str] = []
        self._span_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        amap = {k.lower(): (v or "") for k, v in attrs if k}
        if tag == "svg":
            if self._in_a:
                self._in_svg += 1
            return
        if tag == "a" and not self._in_a:
            href = amap.get("href", "")
            path = urlparse(href).path if href.startswith("http") else href
            if _NEWS_HREF_RE.match(path):
                self._in_a = True
                self._depth = 1
                self._href = path
                self._datetime = None
                self._texts = []
                self._span_texts = []
                self._cur_span = []
                self._span_depth = 0
                self._in_time = False
                self._in_svg = 0
                return
        if not self._in_a:
            return
        if tag == "a":
            self._depth += 1
        if tag == "time":
            self._in_time = True
            self._datetime = amap.get("datetime") or amap.get("datetime".lower()) or None
        if tag == "span":
            if self._span_depth == 0:
                self._cur_span = []
            self._span_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg" and self._in_a and self._in_svg:
            self._in_svg -= 1
            return
        if not self._in_a:
            return
        if tag == "time":
            self._in_time = False
        if tag == "span" and self._span_depth:
            self._span_depth -= 1
            if self._span_depth == 0:
                text = collapse_ws("".join(self._cur_span))
                if text:
                    self._span_texts.append(text)
                self._cur_span = []
        if tag == "a":
            self._depth -= 1
            if self._depth <= 0:
                self._flush()
                self._in_a = False

    def handle_data(self, data: str) -> None:
        if not self._in_a or self._in_svg:
            return
        if self._span_depth:
            self._cur_span.append(data)
        else:
            self._texts.append(data)

    def _flush(self) -> None:
        if not self._href:
            return
        title = self._span_texts[0] if self._span_texts else collapse_ws("".join(self._texts))
        lede = self._span_texts[1] if len(self._span_texts) > 1 else None
        if not title:
            return
        path = self._href if self._href.endswith("/") else self._href + "/"
        self.items.append({
            "id": news_id_from_path(self._href),
            "title": title,
            "lede": lede,
            "source_name": SOURCE_OFFICIAL,
            "source_url": "https://omarchy.org" + path,
            "published_at": self._datetime,
        })


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: Optional[str] = None
        self.published_at: Optional[str] = None
        self.og_image: Optional[str] = None
        self.og_title: Optional[str] = None
        self._in_h1 = False
        self._h1: List[str] = []
        self._in_prose = False
        self._prose_depth = 0
        self._prose_html: List[str] = []
        self._capture_prose = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        amap = {k.lower(): (v or "") for k, v in attrs if k}
        if tag == "meta":
            prop = amap.get("property") or amap.get("name") or ""
            content = amap.get("content") or ""
            if prop == "og:image" and content:
                self.og_image = content
            if prop == "og:title" and content:
                self.og_title = content
            return
        if tag == "time" and amap.get("datetime"):
            self.published_at = self.published_at or amap.get("datetime")
        if tag == "h1" and self.title is None:
            self._in_h1 = True
            self._h1 = []
        cls = amap.get("class") or ""
        if tag == "div" and "prose" in cls.split() and not self._capture_prose:
            self._capture_prose = True
            self._in_prose = True
            self._prose_depth = 1
            return
        if self._in_prose:
            self._prose_depth += 1
            attr_s = "".join(
                f' {k}="{html.escape(v, quote=True)}"' for k, v in attrs if k and v is not None
            )
            self._prose_html.append(f"<{tag}{attr_s}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            text = collapse_ws("".join(self._h1))
            if text:
                self.title = text
        if self._in_prose:
            if tag not in _VOID:
                self._prose_html.append(f"</{tag}>")
            self._prose_depth -= 1
            if self._prose_depth <= 0:
                self._in_prose = False

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1.append(data)
        if self._in_prose:
            self._prose_html.append(html.escape(data, quote=False))

    @property
    def body_html(self) -> Optional[str]:
        raw = "".join(self._prose_html).strip()
        return raw or None


def parse_omarchy_news_html(document: str, *, fetched_at: Optional[str] = None) -> List[Dict[str, Any]]:
    parser = _NewsIndexParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for raw in parser.items:
        raw["fetched_at"] = fetched_at
        item = normalize_item(raw, fetched_at=fetched_at)
        if item:
            out.append(item)
    return out


def parse_omarchy_article_html(
    document: str,
    *,
    source_url: str,
    fetched_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    parser = _ArticleParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception:
        return None
    title = parser.title or parser.og_title
    if title and title.endswith(" - Omarchy News"):
        title = title[: -len(" - Omarchy News")].strip()
    path = urlparse(source_url).path
    raw = {
        "id": news_id_from_path(path),
        "title": title,
        "body_html": parser.body_html,
        "image_url": parser.og_image,
        "source_name": SOURCE_OFFICIAL,
        "source_url": source_url,
        "published_at": parser.published_at,
        "fetched_at": fetched_at,
    }
    return normalize_item(raw, fetched_at=fetched_at)


# ---------------------------------------------------------------------------
# RSS / Atom / GitHub
# ---------------------------------------------------------------------------

def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_text(el: ET.Element, names: Sequence[str]) -> Optional[str]:
    want = {n.lower() for n in names}
    for child in list(el):
        if _local(child.tag).lower() in want:
            text = "".join(child.itertext()).strip()
            if text:
                return text
            if child.text and child.text.strip():
                return child.text.strip()
    return None


def _child_html(el: ET.Element, names: Sequence[str]) -> Optional[str]:
    want = [n.lower() for n in names]
    found: Dict[str, str] = {}
    for child in list(el):
        loc = _local(child.tag).lower()
        if loc not in want:
            continue
        inner = "".join(ET.tostring(c, encoding="unicode") for c in list(child))
        if child.text and not list(child):
            found[loc] = child.text
        elif inner.strip():
            found[loc] = (child.text or "") + inner
        elif child.text:
            found[loc] = child.text
    for name in want:
        if found.get(name):
            return found[name]
    return None


def parse_rss(
    document: str,
    *,
    source_name: str,
    kind: str,
    fetched_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    items: List[Dict[str, Any]] = []
    for el in root.iter():
        if _local(el.tag).lower() != "item":
            continue
        title = _child_text(el, ["title"])
        link = _child_text(el, ["link"])
        guid = _child_text(el, ["guid"])
        pub = _child_text(el, ["pubDate", "date", "published"])
        desc = _child_html(el, ["encoded", "description", "content"])
        # content:encoded localname is "encoded"
        image = None
        for child in list(el):
            if _local(child.tag).lower() == "enclosure":
                url = child.attrib.get("url")
                typ = child.attrib.get("type") or ""
                if url and typ.startswith("image"):
                    image = url
        body_html = desc if desc and ("<" in desc) else None
        body_text = strip_tags(desc) if desc else None
        ident_key = guid or link or title or ""
        raw = {
            "id": slug_id(kind, ident_key),
            "title": title,
            "lede": body_text,
            "body_html": body_html,
            "body_text": body_text,
            "image_url": image,
            "source_name": source_name,
            "source_url": link or guid,
            "published_at": pub,
            "fetched_at": fetched_at,
        }
        item = normalize_item(raw, fetched_at=fetched_at)
        if item:
            items.append(item)
    return items


def parse_atom(
    document: str,
    *,
    source_name: str,
    kind: str,
    fetched_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return []
    items: List[Dict[str, Any]] = []
    for el in root.iter():
        if _local(el.tag).lower() != "entry":
            continue
        title = _child_text(el, ["title"])
        link = None
        image = None
        for child in list(el):
            loc = _local(child.tag).lower()
            if loc == "link":
                href = child.attrib.get("href")
                rel = child.attrib.get("rel") or "alternate"
                if href and rel in {"alternate", ""}:
                    link = link or href
                elif href and not link:
                    link = href
            if loc == "thumbnail":
                image = child.attrib.get("url") or image
        ident = _child_text(el, ["id"]) or link or title or ""
        pub = _child_text(el, ["published", "updated"])
        content = _child_html(el, ["content", "summary"])
        if content:
            content = html.unescape(content)
        if not image and content:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content, re.I)
            if m:
                image = html.unescape(m.group(1))
        body_html = content if content and "<" in content else None
        body_text = strip_tags(content) if content else None
        raw = {
            "id": slug_id(kind, ident),
            "title": title,
            "lede": body_text,
            "body_html": body_html,
            "body_text": body_text,
            "image_url": image,
            "source_name": source_name,
            "source_url": link or ident,
            "published_at": pub,
            "fetched_at": fetched_at,
        }
        item = normalize_item(raw, fetched_at=fetched_at)
        if item:
            items.append(item)
    return items


def parse_github_releases(payload: Any, *, fetched_at: Optional[str] = None) -> List[Dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    items: List[Dict[str, Any]] = []
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        if rec.get("draft"):
            continue
        tag = rec.get("tag_name") or ""
        name = rec.get("name") or tag
        body = rec.get("body") or ""
        url = rec.get("html_url") or rec.get("url")
        ident = str(rec.get("id") or tag or name)
        raw = {
            "id": slug_id("release", ident),
            "title": name,
            "lede": lede_from_text(body),
            "body_html": markdown_to_html(body),
            "body_text": collapse_ws(body) if body else None,
            "image_url": None,
            "source_name": SOURCE_RELEASES,
            "source_url": url,
            "published_at": rec.get("published_at") or rec.get("created_at"),
            "fetched_at": fetched_at,
        }
        item = normalize_item(raw, fetched_at=fetched_at)
        if item:
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------

def hermes_home(root: Optional[Path] = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


def cache_dir(root: Optional[Path] = None) -> Path:
    d = hermes_home(root) / "cache" / PLUGIN
    d.mkdir(parents=True, exist_ok=True)
    return d


def feed_cache_path(root: Optional[Path] = None) -> Path:
    return cache_dir(root) / "feed.json"


def article_cache_path(item_id: str, root: Optional[Path] = None) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._:-]+", "_", item_id)[:180]
    return cache_dir(root) / "articles" / f"{safe}.json"


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def default_http_get(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str, Dict[str, str]]:
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs, method="GET")
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = body.decode(charset, errors="replace")
            info = {k.lower(): v for k, v in resp.headers.items()}
            return int(getattr(resp, "status", 200) or 200), text, info
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        raise OSError(f"HTTP {exc.code} for {url}: {err_body[:180]}") from exc
    except URLError as exc:
        raise OSError(f"network error for {url}: {exc.reason}") from exc


def _record_error(
    errors: List[Dict[str, Any]],
    *,
    kind: str,
    path: Any,
    error: Any,
    required: bool = True,
) -> None:
    errors.append({
        "kind": kind,
        "path": str(path) if path is not None else None,
        "error": str(error),
        "required": required,
    })


def fetch_official_news(
    getter: HttpGetter,
    *,
    fetched_at: str,
    errors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        status, body, _ = getter(OFFICIAL_NEWS_URL, None)
        if status >= 400:
            raise OSError(f"HTTP {status}")
        items = parse_omarchy_news_html(body, fetched_at=fetched_at)
        if not items and re.search(r"/news/\d{4}/\d{2}/", body):
            # Links were on the page but the card parser missed them — markup drift,
            # not a quiet day.
            _record_error(
                errors, kind="empty_parse", path=OFFICIAL_NEWS_URL,
                error="HTML listing contained news links the parser did not extract",
                required=True,
            )
    except Exception as exc:
        _record_error(errors, kind="official_html", path=OFFICIAL_NEWS_URL, error=exc, required=True)

    if items:
        return items

    try:
        status, body, _ = getter(OFFICIAL_RSS_URL, {"Accept": "application/rss+xml, application/xml, text/xml"})
        if status >= 400:
            raise OSError(f"HTTP {status}")
        rss_items = parse_rss(body, source_name=SOURCE_OFFICIAL, kind="news", fetched_at=fetched_at)
        # Normalize RSS ids to the news:YYYY-MM-slug form when the URL matches.
        for it in rss_items:
            path = urlparse(it.get("source_url") or "").path
            if _NEWS_HREF_RE.match(path if path.endswith("/") else path + "/") or _NEWS_HREF_RE.match(path):
                it["id"] = news_id_from_path(path)
        if rss_items:
            # RSS fallback recovered after empty/failed HTML — this is still official.
            errors[:] = [
                e for e in errors
                if e.get("kind") not in {"empty_parse", "official_html"}
            ]
            return rss_items
        if re.search(r"<item[\s>]", body, re.I) and not rss_items:
            _record_error(
                errors, kind="empty_parse", path=OFFICIAL_RSS_URL,
                error="RSS contained items the parser did not extract",
                required=True,
            )
    except Exception as exc:
        _record_error(errors, kind="official_rss", path=OFFICIAL_RSS_URL, error=exc, required=True)
    return items


def fetch_github_releases(
    getter: HttpGetter,
    *,
    fetched_at: str,
    errors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        status, body, _ = getter(
            GITHUB_RELEASES_URL,
            {"Accept": "application/vnd.github+json"},
        )
        if status >= 400:
            raise OSError(f"HTTP {status}")
        items = parse_github_releases(body, fetched_at=fetched_at)
        return items
    except Exception as exc:
        _record_error(errors, kind="github_releases", path=GITHUB_RELEASES_URL, error=exc, required=True)
        return []


def fetch_optional_rss(
    getter: HttpGetter,
    *,
    url: str,
    source_name: str,
    kind: str,
    atom: bool,
    fetched_at: str,
    errors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    try:
        status, body, _ = getter(url, {"Accept": "application/atom+xml, application/rss+xml, application/xml"})
        if status >= 400:
            raise OSError(f"HTTP {status}")
        if atom or "<feed" in body[:400]:
            return parse_atom(body, source_name=source_name, kind=kind, fetched_at=fetched_at)
        return parse_rss(body, source_name=source_name, kind=kind, fetched_at=fetched_at)
    except Exception as exc:
        _record_error(errors, kind=kind, path=url, error=exc, required=False)
        return []


def collect_feed(
    *,
    getter: Optional[HttpGetter] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
    ttl: int = FEED_TTL_SECONDS,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Aggregate sources. Network-free when a fresh cache exists (unless refresh)."""
    getter = getter or default_http_get
    as_of = now.astimezone(timezone.utc) if now is not None else utcnow()
    fetched_at = parse_datetime(as_of) or as_of.isoformat().replace("+00:00", "Z")
    path = feed_cache_path(root)
    cached = load_json(path)
    cache_age: Optional[float] = None
    if cached and cached.get("fetched_at"):
        try:
            cached_dt = datetime.fromisoformat(str(cached["fetched_at"]).replace("Z", "+00:00"))
            cache_age = (as_of - cached_dt.astimezone(timezone.utc)).total_seconds()
        except ValueError:
            cache_age = None

    if (
        not refresh
        and cached
        and cache_age is not None
        and 0 <= cache_age < ttl
        and isinstance(cached.get("items"), list)
    ):
        payload = dict(cached)
        payload["stale"] = False
        payload["cache_age_seconds"] = int(cache_age)
        payload["from_cache"] = True
        return payload

    errors: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    all_items: List[Dict[str, Any]] = []

    def add_source(name: str, items: List[Dict[str, Any]], required: bool) -> None:
        sources.append({
            "name": name,
            "count": len(items),
            "required": required,
        })
        all_items.extend(items)

    official = fetch_official_news(getter, fetched_at=fetched_at, errors=errors)
    add_source(SOURCE_OFFICIAL, official, True)

    releases = fetch_github_releases(getter, fetched_at=fetched_at, errors=errors)
    add_source(SOURCE_RELEASES, releases, True)

    reddit = fetch_optional_rss(
        getter, url=REDDIT_RSS_URL, source_name=SOURCE_REDDIT, kind="reddit",
        atom=True, fetched_at=fetched_at, errors=errors,
    )
    add_source(SOURCE_REDDIT, reddit, False)

    hn = fetch_optional_rss(
        getter, url=HN_RSS_URL, source_name=SOURCE_HN, kind="hn",
        atom=False, fetched_at=fetched_at, errors=errors,
    )
    add_source(SOURCE_HN, hn, False)

    # Attach per-source error strings.
    err_by_kind = {}
    for e in errors:
        err_by_kind.setdefault(e.get("kind"), e.get("error"))
    kind_for_name = {
        SOURCE_OFFICIAL: ("official_html", "official_rss", "empty_parse"),
        SOURCE_RELEASES: ("github_releases",),
        SOURCE_REDDIT: ("reddit",),
        SOURCE_HN: ("hn",),
    }
    for src in sources:
        for kind in kind_for_name.get(src["name"], ()):
            if kind in err_by_kind:
                src["error"] = err_by_kind[kind]
                src["ok"] = False
                break
        else:
            src["ok"] = True

    items = sort_items(dedupe_items(all_items))
    required_errors = [e for e in errors if e.get("required")]
    optional_errors = [e for e in errors if not e.get("required")]

    if items:
        read_status = "partial" if required_errors else "ok"
        ok = True
        payload = _feed_payload(
            ok=ok,
            stale=False,
            read_status=read_status,
            items=items,
            sources=sources,
            errors=required_errors,
            warnings=optional_errors,
            fetched_at=fetched_at,
            cache_age_seconds=0,
            from_cache=False,
        )
        try:
            save_json(path, payload)
        except OSError as exc:
            payload["errors"] = list(payload.get("errors") or []) + [{
                "kind": "cache_write", "path": str(path), "error": str(exc), "required": False,
            }]
        return payload

    # No fresh items. Serve cache if we have any (refresh failed or sources empty
    # after a previously populated cache).
    cached_items = []
    if cached and isinstance(cached.get("items"), list):
        cached_items = [i for i in cached["items"] if isinstance(i, dict)]
    if cached_items and required_errors:
        age = int(cache_age) if cache_age is not None else None
        return _feed_payload(
            ok=True,
            stale=True,
            read_status="stale",
            items=cached_items,
            sources=cached.get("sources") or sources,
            errors=required_errors,
            warnings=optional_errors,
            fetched_at=cached.get("fetched_at") or fetched_at,
            cache_age_seconds=age,
            from_cache=True,
        )

    if not required_errors:
        # Honest empty: every required source answered and had nothing to show.
        payload = _feed_payload(
            ok=True,
            stale=False,
            read_status="ok",
            items=[],
            sources=sources,
            errors=[],
            warnings=optional_errors,
            fetched_at=fetched_at,
            cache_age_seconds=0,
            from_cache=False,
        )
        try:
            save_json(path, payload)
        except OSError:
            pass
        return payload

    # Total failure, nothing to show.
    return _feed_payload(
        ok=False,
        stale=False,
        read_status="unread",
        items=[],
        sources=sources,
        errors=required_errors or errors or [{
            "kind": "feed", "path": None, "error": "no sources returned items", "required": True,
        }],
        warnings=optional_errors,
        fetched_at=fetched_at,
        cache_age_seconds=None,
        from_cache=False,
    )


def _feed_payload(
    *,
    ok: bool,
    stale: bool,
    read_status: str,
    items: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    fetched_at: str,
    cache_age_seconds: Optional[int],
    from_cache: bool,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "plugin": PLUGIN,
        "stale": stale,
        "read_status": read_status,
        "empty": ok and not items and not stale,
        "items": items,
        "count": len(items),
        "sources": sources,
        "errors": errors,
        "warnings": warnings,
        "fetched_at": fetched_at,
        "cache_age_seconds": cache_age_seconds,
        "from_cache": from_cache,
    }


def find_cached_item(item_id: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    cached = load_json(feed_cache_path(root))
    if not cached:
        return None
    for it in cached.get("items") or []:
        if isinstance(it, dict) and it.get("id") == item_id:
            return it
    return None


def article_has_body(item: Optional[Dict[str, Any]]) -> bool:
    if not item:
        return False
    html_body = (item.get("body_html") or "").strip()
    text_body = (item.get("body_text") or "").strip()
    lede = (item.get("lede") or "").strip()
    # Listing ledes are short; require more than the lede to count as full body.
    if html_body and strip_tags(html_body) and len(strip_tags(html_body)) > max(len(lede), 80):
        return True
    if text_body and len(text_body) > max(len(lede), 80):
        return True
    return False


def get_article(
    item_id: str,
    *,
    getter: Optional[HttpGetter] = None,
    root: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    getter = getter or default_http_get
    as_of = now.astimezone(timezone.utc) if now is not None else utcnow()
    fetched_at = parse_datetime(as_of) or as_of.isoformat().replace("+00:00", "Z")
    art_path = article_cache_path(item_id, root)
    disk = load_json(art_path)
    if disk and article_has_body(disk.get("item") if "item" in disk else disk):
        item = disk.get("item") if "item" in disk else disk
        return {"ok": True, "plugin": PLUGIN, "item": item, "from_cache": True, "stale": False}

    item = find_cached_item(item_id, root)
    if item and article_has_body(item) and not _is_official_news(item):
        # Releases / RSS already carry a body.
        try:
            save_json(art_path, {"item": item, "fetched_at": fetched_at})
        except OSError:
            pass
        return {"ok": True, "plugin": PLUGIN, "item": item, "from_cache": True, "stale": False}

    if item and _is_official_news(item) and item.get("source_url"):
        url = str(item["source_url"])
        if not host_allowed(url):
            return {
                "ok": False, "plugin": PLUGIN, "item": item,
                "errors": [{"kind": "ssrf", "path": url, "error": "host not allowlisted"}],
            }
        try:
            status, body, _ = getter(url, None)
            if status >= 400:
                raise OSError(f"HTTP {status}")
            parsed = parse_omarchy_article_html(body, source_url=url, fetched_at=fetched_at)
            if parsed and parsed.get("title"):
                # Keep listing id; fill body/image from the page. Never invent title
                # if parse title differs — prefer page title when present.
                merged = dict(item)
                merged["title"] = parsed["title"]
                merged["body_html"] = parsed.get("body_html")
                merged["body_text"] = parsed.get("body_text")
                if parsed.get("lede"):
                    merged["lede"] = parsed["lede"]
                if parsed.get("image_url"):
                    merged["image_url"] = parsed["image_url"]
                if parsed.get("published_at"):
                    merged["published_at"] = parsed["published_at"]
                merged["fetched_at"] = fetched_at
                try:
                    save_json(art_path, {"item": merged, "fetched_at": fetched_at})
                except OSError:
                    pass
                return {"ok": True, "plugin": PLUGIN, "item": merged, "from_cache": False, "stale": False}
            # Parse failed — return listing item, do not fabricate prose.
            return {
                "ok": True, "plugin": PLUGIN, "item": item, "from_cache": True,
                "stale": False, "partial": True,
                "errors": [{"kind": "article_parse", "path": url, "error": "article HTML had no body"}],
            }
        except Exception as exc:
            if item:
                return {
                    "ok": True, "plugin": PLUGIN, "item": item, "from_cache": True,
                    "stale": True,
                    "errors": [{"kind": "article_fetch", "path": url, "error": str(exc)}],
                }
            return {
                "ok": False, "plugin": PLUGIN, "item": None,
                "errors": [{"kind": "article_fetch", "path": url, "error": str(exc)}],
            }

    if item:
        return {"ok": True, "plugin": PLUGIN, "item": item, "from_cache": True, "stale": False}

    return {
        "ok": False, "plugin": PLUGIN, "item": None,
        "errors": [{"kind": "not_found", "path": item_id, "error": "unknown article id"}],
    }


def _is_official_news(item: Dict[str, Any]) -> bool:
    url = str(item.get("source_url") or "")
    path = urlparse(url).path
    return item.get("source_name") == SOURCE_OFFICIAL and bool(
        _NEWS_HREF_RE.match(path if path.endswith("/") else path + "/") or _NEWS_HREF_RE.match(path)
    )


def _json(payload: Dict[str, Any], status: int = 200):
    if JSONResponse is None:
        return payload
    return JSONResponse(payload, status_code=status)


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "refresh"}


if router is not None:

    @router.get("/feed")
    def feed(refresh: Optional[str] = None):
        try:
            payload = collect_feed(refresh=_truthy(refresh))
        except Exception as exc:  # pragma: no cover - defensive mount
            return _json({
                "ok": False,
                "plugin": PLUGIN,
                "stale": False,
                "read_status": "unread",
                "empty": False,
                "items": [],
                "count": 0,
                "sources": [],
                "errors": [{"kind": "feed", "path": None, "error": str(exc), "required": True}],
                "warnings": [],
                "fetched_at": parse_datetime(utcnow()),
                "cache_age_seconds": None,
                "from_cache": False,
            }, status=200)
        return _json(payload)

    @router.get("/article")
    def article_query(id: Optional[str] = None):
        if not id:
            return _json({
                "ok": False, "plugin": PLUGIN, "item": None,
                "errors": [{"kind": "bad_request", "path": None, "error": "missing id"}],
            }, status=200)
        try:
            return _json(get_article(id))
        except Exception as exc:  # pragma: no cover
            return _json({
                "ok": False, "plugin": PLUGIN, "item": None,
                "errors": [{"kind": "article", "path": id, "error": str(exc)}],
            }, status=200)

    @router.get("/article/{item_id:path}")
    def article_path(item_id: str):
        try:
            return _json(get_article(item_id))
        except Exception as exc:  # pragma: no cover
            return _json({
                "ok": False, "plugin": PLUGIN, "item": None,
                "errors": [{"kind": "article", "path": item_id, "error": str(exc)}],
            }, status=200)

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok", "plugin": PLUGIN}
