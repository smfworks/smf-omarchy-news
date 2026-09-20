"""Parse / normalize / cache honesty for SMF Omarchy News."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import plugin_api as api

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Normalize / parse
# ---------------------------------------------------------------------------

def test_normalize_item_requires_title_and_source():
    assert api.normalize_item({"title": "", "source_name": "Omarchy.org"}) is None
    assert api.normalize_item({"title": "Hello", "source_name": ""}) is None
    item = api.normalize_item({
        "id": "news:x",
        "title": "  Introducing Omarchy Dragon  ",
        "lede": "We should have teams responsible for every major platform.",
        "source_name": "Omarchy.org",
        "source_url": "https://omarchy.org/news/2026/09/introducing-omarchy-dragon/",
        "published_at": "2026-09-18T08:00:00+02:00",
    })
    assert item is not None
    assert item["title"] == "Introducing Omarchy Dragon"
    assert item["source_name"] == "Omarchy.org"
    assert item["published_at"].startswith("2026-09-18T")
    assert item["lede"].startswith("We should have teams")
    assert item["body_html"] is None
    assert item["image_url"] is None


def test_normalize_item_never_invents_lede_or_body():
    item = api.normalize_item({
        "id": "news:bare",
        "title": "Bare title",
        "source_name": "Omarchy.org",
        "source_url": "https://omarchy.org/news/2026/09/bare/",
    })
    assert item["lede"] is None
    assert item["body_html"] is None
    assert item["body_text"] is None
    assert item["published_at"] is None


def test_lede_truncates_without_rewriting():
    text = "Alpha bravo charlie " * 40
    lede = api.lede_from_text(text, limit=80)
    assert lede.endswith("…")
    assert "Alpha bravo" in lede
    assert "invented" not in lede.lower()


def test_parse_omarchy_news_html_extracts_cards():
    items = api.parse_omarchy_news_html(_read("news_index.html"), fetched_at="2026-09-20T00:00:00Z")
    assert [i["title"] for i in items] == [
        "Introducing Omarchy Dragon",
        "OrcaRouter joins as a Distinguished Corporate Patron",
    ]
    dragon = items[0]
    assert dragon["id"] == "news:2026-09-introducing-omarchy-dragon"
    assert dragon["source_name"] == "Omarchy.org"
    assert dragon["source_url"].endswith("/news/2026/09/introducing-omarchy-dragon/")
    assert "Snapdragon" in (dragon["lede"] or "")
    assert dragon["published_at"].startswith("2026-09-18T")


def test_parse_omarchy_article_html_keeps_prose_strips_script():
    item = api.parse_omarchy_article_html(
        _read("news_article.html"),
        source_url="https://omarchy.org/news/2026/09/introducing-omarchy-dragon/",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert item is not None
    assert item["title"] == "Introducing Omarchy Dragon"
    assert item["image_url"] == "https://omarchy.org/brand/social/hackerman.png"
    assert "Five people are already working" in (item["body_text"] or "")
    assert "script" not in (item["body_html"] or "").lower()
    assert "alert" not in (item["body_html"] or "")
    assert "/brand/dragon.png" in (item["body_html"] or "") or "dragon.png" in (item["body_html"] or "")
    # og:image is a different file from the body photo — not a duplicate.
    assert item["image_in_body"] is False


def test_parse_rss_official():
    items = api.parse_rss(
        _read("news_rss.xml"),
        source_name="Omarchy.org",
        kind="news",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert len(items) == 1
    assert items[0]["title"] == "Introducing Omarchy Dragon"
    assert "Full RSS body" in (items[0]["body_text"] or "")


def test_parse_github_releases_skips_drafts():
    items = api.parse_github_releases(
        _read("github_releases.json"),
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert len(items) == 1
    assert items[0]["title"] == "v4.0.4"
    assert items[0]["source_name"] == "Omarchy releases"
    assert "bespoke kernel" in (items[0]["lede"] or "")
    assert items[0]["id"].startswith("release:")
    assert "webcam" in (items[0]["body_text"] or "")
    assert items[0]["image_url"] is None
    assert items[0]["image_in_body"] is False


def test_parse_atom_reddit_labeled_community():
    items = api.parse_atom(
        _read("reddit.atom"),
        source_name="Reddit r/omarchy",
        kind="reddit",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert len(items) == 1
    assert items[0]["source_name"] == "Reddit r/omarchy"
    assert "QUATTRO" in items[0]["title"]
    assert items[0]["image_url"] and "preview.redd.it" in items[0]["image_url"]
    # Same preview was lifted into image_url; the duplicate <img> is stripped
    # from displayed body_html so the reader can show the hero once.
    assert items[0]["image_in_body"] is True
    assert "preview.redd.it" not in (items[0]["body_html"] or "")
    assert "<img" not in (items[0]["body_html"] or "").lower()


def test_parse_hn_rss_labeled():
    items = api.parse_rss(
        _read("hn.rss"),
        source_name="Hacker News",
        kind="hn",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert items[0]["source_name"] == "Hacker News"
    assert items[0]["source_url"].startswith("https://news.ycombinator.com/")


def test_sanitize_html_drops_scripts_and_keeps_links():
    raw = '<p>Hi <a href="/news/x/">x</a></p><script>alert(1)</script>'
    out = api.sanitize_html(raw)
    assert "script" not in (out or "").lower()
    assert "alert" not in (out or "")
    assert "https://omarchy.org/news/x/" in (out or "")


def test_sort_items_newest_first_missing_dates_last():
    items = [
        {"id": "a", "published_at": "2026-09-01T00:00:00Z"},
        {"id": "b", "published_at": None},
        {"id": "c", "published_at": "2026-09-18T00:00:00Z"},
    ]
    ordered = api.sort_items(items)
    assert [i["id"] for i in ordered] == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# HTTP fixture getter + cache / empty vs error
# ---------------------------------------------------------------------------

def _getter_from_map(mapping):
    def getter(url, headers=None):
        if url not in mapping:
            raise OSError(f"unexpected url {url}")
        spec = mapping[url]
        if isinstance(spec, Exception):
            raise spec
        status, body = spec
        if status >= 400:
            raise OSError(f"HTTP {status} for {url}")
        return status, body, {}
    return getter


EMPTY_HTML = "<html><body><h1>News</h1><ul></ul></body></html>"
EMPTY_RSS = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
EMPTY_GH = "[]"
EMPTY_ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>'
BROKEN_HTML_WITH_LINKS = (
    '<html><body><script type="application/json">'
    '{"url":"/news/2026/09/mystery-post/","title":"Mystery"}'
    "</script><p>News index rebuilt; cards are no longer in markup.</p></body></html>"
)


def _all_empty_map():
    return {
        api.OFFICIAL_NEWS_URL: (200, EMPTY_HTML),
        api.OFFICIAL_RSS_URL: (200, EMPTY_RSS),
        api.GITHUB_RELEASES_URL: (200, EMPTY_GH),
        api.REDDIT_RSS_URL: (200, EMPTY_ATOM),
        api.HN_RSS_URL: (200, EMPTY_RSS),
    }


def test_empty_successful_read_is_ok_not_error(tmp_path: Path):
    payload = api.collect_feed(
        getter=_getter_from_map(_all_empty_map()),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        ttl=900,
        refresh=True,
    )
    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["empty"] is True
    assert payload["stale"] is False
    assert payload["read_status"] == "ok"
    assert payload["errors"] == []


def test_html_markup_drift_is_error_not_empty(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, BROKEN_HTML_WITH_LINKS)
    mapping[api.OFFICIAL_RSS_URL] = (200, EMPTY_RSS)
    payload = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    assert payload["ok"] is False
    assert payload["items"] == []
    assert payload["read_status"] == "unread"
    kinds = [e["kind"] for e in payload["errors"]]
    assert "empty_parse" in kinds


def test_required_source_failure_without_cache_is_unread(tmp_path: Path):
    mapping = {
        api.OFFICIAL_NEWS_URL: OSError("down"),
        api.OFFICIAL_RSS_URL: OSError("down"),
        api.GITHUB_RELEASES_URL: OSError("down"),
        api.REDDIT_RSS_URL: OSError("down"),
        api.HN_RSS_URL: OSError("down"),
    }
    payload = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    assert payload["ok"] is False
    assert payload["items"] == []
    assert payload["stale"] is False
    assert payload["read_status"] == "unread"
    assert payload["errors"]
    titles = [i.get("title") for i in payload["items"]]
    assert titles == []


def test_cache_stale_flag_on_refresh_failure(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, _read("news_index.html"))
    mapping[api.GITHUB_RELEASES_URL] = (200, _read("github_releases.json"))
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    fresh = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=now,
        ttl=900,
        refresh=True,
    )
    assert fresh["ok"] is True
    assert fresh["stale"] is False
    assert len(fresh["items"]) >= 3
    cached_titles = {i["title"] for i in fresh["items"]}

    fail = {
        api.OFFICIAL_NEWS_URL: OSError("timeout"),
        api.OFFICIAL_RSS_URL: OSError("timeout"),
        api.GITHUB_RELEASES_URL: OSError("timeout"),
        api.REDDIT_RSS_URL: OSError("timeout"),
        api.HN_RSS_URL: OSError("timeout"),
    }
    later = now + timedelta(hours=1)
    stale = api.collect_feed(
        getter=_getter_from_map(fail),
        root=tmp_path,
        now=later,
        ttl=900,
        refresh=True,
    )
    assert stale["ok"] is True
    assert stale["stale"] is True
    assert stale["from_cache"] is True
    assert stale["cache_age_seconds"] == pytest.approx(3600, abs=1)
    assert {i["title"] for i in stale["items"]} == cached_titles
    assert stale["errors"]
    # Did not invent a new headline to cover the outage.
    assert "Invented" not in {i["title"] for i in stale["items"]}


def test_ttl_serves_cache_without_network(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, _read("news_index.html"))
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    first = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=now,
        ttl=900,
        refresh=True,
    )
    assert first["from_cache"] is False

    def boom(url, headers=None):
        raise AssertionError(f"network should not run for {url}")

    second = api.collect_feed(
        getter=boom,
        root=tmp_path,
        now=now + timedelta(minutes=2),
        ttl=900,
        refresh=False,
    )
    assert second["from_cache"] is True
    assert second["stale"] is False
    assert second["items"][0]["title"] == first["items"][0]["title"]


def test_optional_source_failure_does_not_fail_feed(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, _read("news_index.html"))
    mapping[api.GITHUB_RELEASES_URL] = (200, _read("github_releases.json"))
    mapping[api.REDDIT_RSS_URL] = OSError("429")
    mapping[api.HN_RSS_URL] = OSError("502")
    payload = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    assert payload["ok"] is True
    assert payload["stale"] is False
    assert payload["errors"] == []
    assert payload["warnings"]
    names = {i["source_name"] for i in payload["items"]}
    assert "Omarchy.org" in names
    assert "Omarchy releases" in names
    assert "Reddit r/omarchy" not in names


def test_get_article_fetches_official_body_from_listing(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, _read("news_index.html"))
    article_url = "https://omarchy.org/news/2026/09/introducing-omarchy-dragon/"
    mapping[article_url] = (200, _read("news_article.html"))
    api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    result = api.get_article(
        "news:2026-09-introducing-omarchy-dragon",
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, 1, tzinfo=timezone.utc),
    )
    assert result["ok"] is True
    assert "Five people" in (result["item"]["body_text"] or "")
    assert result["item"]["image_url"].endswith("hackerman.png")
    assert result["item"]["image_in_body"] is False


def test_get_article_does_not_fabricate_when_fetch_fails(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, _read("news_index.html"))
    api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    article_url = "https://omarchy.org/news/2026/09/introducing-omarchy-dragon/"
    fail = dict(mapping)
    fail[article_url] = OSError("404")
    result = api.get_article(
        "news:2026-09-introducing-omarchy-dragon",
        getter=_getter_from_map(fail),
        root=tmp_path,
    )
    assert result["ok"] is True
    assert result["item"]["title"] == "Introducing Omarchy Dragon"
    assert result["item"]["body_html"] is None
    assert result["stale"] is True
    assert result["errors"]
    # Still the listing lede — not a written-up article.
    assert "Five people" not in (result["item"].get("body_text") or "")


def test_get_article_unknown_id_is_error(tmp_path: Path):
    result = api.get_article(
        "news:does-not-exist",
        getter=_getter_from_map({}),
        root=tmp_path,
    )
    assert result["ok"] is False
    assert result["item"] is None
    assert result["errors"][0]["kind"] == "not_found"


def test_github_release_article_uses_cached_body_without_network(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.GITHUB_RELEASES_URL] = (200, _read("github_releases.json"))
    feed = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    rel = next(i for i in feed["items"] if i["source_name"] == "Omarchy releases")

    def boom(url, headers=None):
        raise AssertionError("should not fetch github html")

    result = api.get_article(rel["id"], getter=boom, root=tmp_path)
    assert result["ok"] is True
    assert "bespoke kernel" in (result["item"]["body_text"] or "")


def test_rss_fallback_when_html_empty(tmp_path: Path):
    mapping = _all_empty_map()
    mapping[api.OFFICIAL_NEWS_URL] = (200, EMPTY_HTML)
    mapping[api.OFFICIAL_RSS_URL] = (200, _read("news_rss.xml"))
    payload = api.collect_feed(
        getter=_getter_from_map(mapping),
        root=tmp_path,
        now=datetime(2026, 9, 20, tzinfo=timezone.utc),
        refresh=True,
    )
    assert payload["ok"] is True
    titles = [i["title"] for i in payload["items"] if i["source_name"] == "Omarchy.org"]
    assert titles == ["Introducing Omarchy Dragon"]
    assert payload["items"][0]["id"] == "news:2026-09-introducing-omarchy-dragon"


# ---------------------------------------------------------------------------
# Reader hero vs body_html image dedupe
# ---------------------------------------------------------------------------

def test_image_urls_equivalent_ignores_query_and_relative_paths():
    assert api.image_urls_equivalent(
        "https://omarchy.org/brand/hero.png?w=1200",
        "https://omarchy.org/brand/hero.png",
    )
    assert api.image_urls_equivalent(
        "https://www.omarchy.org/brand/hero.png",
        "http://omarchy.org/brand/hero.png/",
    )
    assert api.image_urls_equivalent(
        "/brand/hero.png",
        "https://omarchy.org/brand/hero.png",
    )
    assert api.image_urls_equivalent(
        "https://cdn.example/photo.jpg",
        "https://cdn.example/photo.jpg/640",
    )
    assert not api.image_urls_equivalent(
        "https://omarchy.org/brand/social/hackerman.png",
        "https://omarchy.org/brand/dragon.png",
    )
    assert not api.image_urls_equivalent(None, "https://omarchy.org/x.png")
    assert not api.image_urls_equivalent("https://omarchy.org/a.png", "https://omarchy.org/b.png")


def test_body_contains_image_does_not_flag_unique_body_photos():
    html = (
        '<p>Caption</p>'
        '<img src="https://omarchy.org/brand/dragon.png" alt="Dragon">'
        '<img src="https://omarchy.org/brand/other.png" alt="Other">'
    )
    assert api.body_contains_image(html, "https://omarchy.org/brand/dragon.png") is True
    assert api.body_contains_image(html, "https://omarchy.org/brand/dragon.png?w=800") is True
    assert api.body_contains_image(html, "https://omarchy.org/brand/social/hackerman.png") is False
    assert api.body_contains_image(html, None) is False
    assert api.body_contains_image(None, "https://omarchy.org/brand/dragon.png") is False
    # No hero → nothing to strip; unique imgs would still render in the body.
    assert api.body_contains_image(html, "") is False


def test_matching_og_image_and_body_img_sets_image_in_body():
    document = """<!DOCTYPE html>
<html>
<head>
<meta property="og:image" content="https://omarchy.org/brand/hero.png?w=1200">
</head>
<body>
<h1>Fixture with shared hero</h1>
<div class="prose">
<p>Enough body text for a full article so this is not just a listing lede placeholder sentence.</p>
<img src="/brand/hero.png" alt="Hero">
<img src="/brand/other.png" alt="Other">
</div>
</body>
</html>
"""
    item = api.parse_omarchy_article_html(
        document,
        source_url="https://omarchy.org/news/2026/09/fixture-shared-hero/",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert item is not None
    assert item["image_url"] == "https://omarchy.org/brand/hero.png?w=1200"
    assert item["image_in_body"] is True
    # Duplicate of the hero is stripped from displayed HTML; the other photo stays.
    assert "hero.png" not in (item["body_html"] or "")
    assert "other.png" in (item["body_html"] or "")


def test_hero_only_article_is_not_image_in_body():
    document = """<!DOCTYPE html>
<html>
<head>
<meta property="og:image" content="https://omarchy.org/brand/social/banner.png">
</head>
<body>
<h1>Hero only</h1>
<div class="prose">
<p>Enough body text for a full article so this is not just a listing lede placeholder sentence.</p>
</div>
</body>
</html>
"""
    item = api.parse_omarchy_article_html(
        document,
        source_url="https://omarchy.org/news/2026/09/fixture-hero-only/",
        fetched_at="2026-09-20T00:00:00Z",
    )
    assert item["image_url"].endswith("banner.png")
    assert item["image_in_body"] is False
    assert "<img" not in (item["body_html"] or "")


def test_strip_duplicate_hero_images_same_matching_rules_as_equivalence():
    html = (
        '<p>Lead</p>'
        '<img src="https://omarchy.org/brand/hero.png?w=1200" alt="Hero">'
        '<img src="https://www.omarchy.org/brand/hero.png" alt="www">'
        '<img src="http://omarchy.org/brand/hero.png/" alt="http">'
        '<img src="/brand/hero.png" alt="relative">'
        '<img src="https://cdn.example/photo.jpg/640" alt="cdn size">'
        '<img src="https://omarchy.org/brand/dragon.png" alt="Dragon">'
        '<img src="https://omarchy.org/brand/other.png" alt="Other">'
    )
    stripped = api.strip_duplicate_hero_images(html, "https://omarchy.org/brand/hero.png")
    assert stripped is not None
    assert "hero.png" not in stripped
    assert "dragon.png" in stripped
    assert "other.png" in stripped
    # Same-host CDN size suffix still counts as the hero.
    cdn = api.strip_duplicate_hero_images(
        '<img src="https://cdn.example/photo.jpg/640" alt="x">'
        '<img src="https://cdn.example/other.jpg" alt="y">',
        "https://cdn.example/photo.jpg",
    )
    assert "photo.jpg" not in (cdn or "")
    assert "other.jpg" in (cdn or "")


def test_strip_duplicate_hero_images_leaves_body_when_no_hero_url():
    html = '<p>Caption</p><img src="https://omarchy.org/brand/dragon.png" alt="Dragon">'
    assert api.strip_duplicate_hero_images(html, None) == html
    assert api.strip_duplicate_hero_images(html, "") == html
    assert api.strip_duplicate_hero_images(None, "https://omarchy.org/x.png") is None


def test_strip_duplicate_hero_images_leaves_distinct_photos_when_hero_absent_from_body():
    html = (
        '<p>Caption</p>'
        '<img src="https://omarchy.org/brand/dragon.png" alt="Dragon">'
        '<img src="https://omarchy.org/brand/other.png" alt="Other">'
    )
    out = api.strip_duplicate_hero_images(
        html, "https://omarchy.org/brand/social/hackerman.png"
    )
    assert "dragon.png" in (out or "")
    assert "other.png" in (out or "")
    assert out == html


def test_strip_duplicate_hero_images_is_idempotent():
    html = (
        '<img src="https://omarchy.org/brand/hero.png" alt="Hero">'
        '<img src="https://omarchy.org/brand/other.png" alt="Other">'
    )
    once = api.strip_duplicate_hero_images(html, "https://omarchy.org/brand/hero.png")
    twice = api.strip_duplicate_hero_images(once, "https://omarchy.org/brand/hero.png")
    assert once == twice
    assert "hero.png" not in (once or "")
    assert "other.png" in (once or "")
