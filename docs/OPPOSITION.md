# Opposition: SMF Omarchy News is not the Omarchy universe

Adversarial review of `smfworks/smf-omarchy-news` at first ship. This pane is a
convenience feed, not a briefing product and not a substitute for omarchy.org,
GitHub, Discord, or the manual. Evidence is from `desktop/plugin.js`,
`dashboard/plugin_api.py`, `install.sh`, and `tests/test_plugin_api.py`.

---

## What this is not

- **Not the complete Omarchy universe.** There is no Discord, no plugin
  marketplace, no wiki, no ISO download counter, no meetup calendar, no
  patronage ledger, and no DHH / Hey World weblog. Those surfaces exist; this
  plugin does not read them. A quiet pane can mean “these sources had nothing
  new,” not “nothing happened in Omarchy.”
- **Not an author.** `plugin_api.py` copies titles, ledes, and bodies from HTML,
  RSS, Atom, or the GitHub Releases JSON. It does not summarize, translate, or
  fill gaps. Missing lede stays missing. A truncated listing ellipsis is stored
  as-is until the article page is fetched.
- **Not live.** The feed is cached under the Hermes home
  (`cache/smf-omarchy-news/`) with a TTL (15 minutes). Opening the pane within
  TTL does not hit the network unless the user clicks Refresh.

---

## STALE rules (must hold)

| Situation | Payload | UI |
|-----------|---------|----|
| Fresh fetch with items | `ok: true`, `stale: false`, `items: […]` | Feed |
| Fresh fetch, every source empty | `ok: true`, `items: []`, `read_status: ok` | EmptyState — not an error |
| Refresh failed, cache present | `ok: true`, `stale: true`, `cache_age_seconds: N`, `errors: […]` | Feed + stale badge with age |
| Refresh failed, no cache | `ok: false`, `items: []`, `errors: […]`, `read_status: unread` | ErrorState — never a fake card |
| Some sources fail, others return items | `ok: true`, `read_status: partial`, `errors` only for **required** sources | Feed + banner |

Never invent items to avoid an empty state. Never paint unread as empty.

---

## HTML scrape fragility

Official news is parsed from `https://omarchy.org/news/` (Astro HTML: `<li><a
href="/news/YYYY/MM/slug/">` + `<time dateTime>` + title span + lede span) and
article pages (`<h1>` + `div.prose`). That markup can change without notice.

Mitigations that are **not** a guarantee:

- RSS at `https://omarchy.org/news/rss.xml` is the same official site, used when
  the HTML listing yields zero items.
- Tests pin today’s markup in `tests/fixtures/`. They will not notice a live
  redesign.
- A parse that returns zero items after HTTP 200 is recorded as a source error
  (`empty_parse`), not a successful empty world.

If omarchy.org ships a JS-only listing, this plugin will go stale-or-error until
the parser is updated. That is preferred to guessing headlines.

Article images are whatever the page advertised (`og:image`, or an `<img>` in
the listing). Many Omarchy news pages reuse a **theme wordmark** as `og:image`,
not a photograph of the story. Broken images become a placeholder; we do not
swap in a different picture.

---

## Source caveats

- **GitHub Releases** are requested from
  `https://api.github.com/repos/basecamp/omarchy/releases`. GitHub currently
  redirects that repo to `omacom/omarchy`. Drafts are skipped. Release notes are
  GitHub Markdown rendered with a **small** subset converter (paragraphs, links,
  headings, lists, code). Complex Markdown (tables, HTML comments) can look
  worse than github.com — Open original is the truth.
- **Reddit r/omarchy** and **Hacker News** (hnrss) are **community** sources.
  They are labeled as such. They are optional: their failure does not fail the
  feed if official items arrived. They are cached with the rest of the feed so
  we do not hammer those hosts. They are not Omarchy Core.
- **DHH / Hey World** has no discoverable RSS from this environment (those URLs
  returned an HTML interstitial). It is not a source. Do not pretend DHH posts
  appear here.
- User-Agent is `SMF-Omarchy-News/1.0`. GitHub and Reddit may still 403/429.
  That is an error or a stale cache, not a reason to fabricate.

---

## Desktop honesty leftovers

- **Enable ≠ mount.** Settings → Plugins is renderer-only. `plugin_api.py`
  mounts on the next `hermes serve`. Reload desktop plugins is JS only.
- **New-count chip** is items with `published_at` after `ctx.storage` last-open.
  First visit shows no chip (everything would look “new”). Clock skew between
  sources is not corrected.
- **Scroll restore** on Back targets a ScrollArea viewport when the SDK exposes
  one. If the kit’s viewport node changes, the feed may reopen at the top.
- **Open original** tries `host.openExternal` / `os.openExternal` / `os.open`
  and then `window.open`. If the host blocks all of those, the URL is still on
  the card as text.
- **No JS tests.** Pytest covers parse/normalize/cache. The pane is untested in
  Electron here.

---

## What already holds (do not redo)

- Disk ESM is valid: `jsx`/`jsxs` only; imports are `@hermes/plugin-sdk`,
  `react`, `react/jsx-runtime`.
- Right pane registers `PANES_AREA` with `placement: 'right'`, `width: '400px'`,
  `dock: { pane: 'workspace', pos: 'right' }`.
- Install script matches Cron Night: enable on `~/.hermes` + `profiles/*/plugins`,
  copy JS to `~/.hermes/desktop-plugins/smf-omarchy-news/`, forbid `hermes desktop`
  on packaged Electron.
- Cache lives on disk under the Hermes home. Tests inject a fake HTTP layer;
  they do not need the network.

Do not treat this pane as Omarchy-ops truth. It is a labeled aggregation of a
few public URLs, willing to say stale or unread when it cannot fetch.
