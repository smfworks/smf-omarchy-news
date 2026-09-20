# SMF Omarchy News — Hermes Desktop Plugin

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) desktop plugin that puts an **Omarchy Linux news feed** in a wide column to the right of chat. Official posts, GitHub releases, and clearly labeled community links — one pane. This is not the complete Omarchy universe. Read [docs/OPPOSITION.md](docs/OPPOSITION.md) before treating it as a briefing.

## What it does

- **Right pane** — Omarchy News, docked to the right of the workspace (`400px`)
- **Sidebar + palette** — Omarchy News, plus ⌘K → Open Omarchy News
- **Cards** — title, lede (first several lines), image when the source provided one, source name, relative time
- **Reader** — click a card to read the article in the same column; Back returns to the feed (scroll is restored when the SDK scroll viewport allows it)
- **Open original** — secondary action; opens the source URL outside Hermes
- **Status bar** — optional chip with an honest new-count (items published after you last opened the pane). No chip on first visit, unread backend, or a failed feed with no cache.
- **Honesty** — empty successful reads stay empty. Fetch failures serve disk cache with `stale: true` and age, or `ok: false` + `errors[]` when there is nothing cached. Headlines are never invented.

## Install

One shot (covers profiles + Desktop JS + enable):

```bash
git clone https://github.com/smfworks/smf-omarchy-news.git ~/.hermes/plugins/smf-omarchy-news
bash ~/.hermes/plugins/smf-omarchy-news/install.sh
```

Or, if Hermes is already on PATH:

```bash
hermes plugins install smfworks/smf-omarchy-news --enable
bash "${HERMES_HOME:-$HOME/.hermes}/plugins/smf-omarchy-news/install.sh"
```

`install.sh` enables the plugin on `$HOME/.hermes` **and** every `profiles/*/plugins` home Desktop may spawn, copies `desktop/plugin.js` into `$HOME/.hermes/desktop-plugins/smf-omarchy-news/` (what packaged Electron actually loads), and tells you to **quit and relaunch Desktop**.

**⌘K → Reload desktop plugins is JS only.** It does not mount `plugin_api.py`. If Omarchy News says **Backend not reachable**, the serve process started before enable — quit Desktop and launch it again.

Do **not** run `hermes desktop` to relaunch if you already have the packaged Linux binary. That command rewrites the `.desktop` `Exec=` and can prompt for `chrome-sandbox` sudo. Use the menu entry / `…/linux-unpacked/Hermes --no-sandbox`.

Then:

1. Settings → Plugins → Omarchy News → on
2. The **Omarchy News** pane on the right of chat, or Sidebar → **Omarchy News**, or ⌘K → Open Omarchy News

### Give this to a Hermes agent

```
Install SMF Omarchy News from https://github.com/smfworks/smf-omarchy-news
Run bash ~/.hermes/plugins/smf-omarchy-news/install.sh (clone first if missing).
Enable on $HOME/.hermes and every profiles/*/ that already has a plugins dir.
Copy desktop/plugin.js to $HOME/.hermes/desktop-plugins/smf-omarchy-news/.
Do not run hermes desktop. Do not kill this chat from inside it.
Tell me to quit Hermes Desktop and relaunch from the menu so plugin_api.py mounts.
```

## Usage

- Scroll the feed. Cards show whatever the source actually published — title, lede, optional image, source, relative time.
- Click a card to read in the column. Official news pages are fetched for full HTML. GitHub releases and community RSS already carry their body from the feed.
- **Back** returns to the feed. **Open original** opens the article URL with the host/os open-external path (falls back to a new window if that RPC is missing).
- **Refresh** forces a network pull. If the pull fails, the last cached feed stays on screen with a **stale** badge and age.

## Architecture

```
smf-omarchy-news/
├── install.sh
├── AGENTS.md
├── plugin.yaml
├── __init__.py
├── dashboard/
│   ├── manifest.json        # api: plugin_api.py
│   └── plugin_api.py        # GET /feed  GET /article
├── desktop/
│   └── plugin.js            # copy to ~/.hermes/desktop-plugins/smf-omarchy-news/
├── docs/
│   └── OPPOSITION.md
└── tests/
    ├── fixtures/
    └── test_plugin_api.py
```

| Route | What it does |
|-------|----------------|
| `GET /feed` | Aggregate sources, newest first. Disk cache under the Hermes home (`cache/smf-omarchy-news/`) with TTL. `?refresh=1` bypasses TTL. Refresh failure → cached items + `stale: true` + `cache_age_seconds`. No cache → `ok: false`, `errors[]`, `items: []`. |
| `GET /article?id=` | Full body from cache, or fetch the official article page. No fabricated prose. Also `GET /article/{id}`. |

Sources (implemented):

| Source | URL | Label |
|--------|-----|--------|
| Official news HTML | `https://omarchy.org/news/` (articles under `/news/YYYY/MM/…`) | Omarchy.org |
| Official RSS fallback | `https://omarchy.org/news/rss.xml` (same site, used if HTML parse is empty) | Omarchy.org |
| GitHub Releases | `https://api.github.com/repos/basecamp/omarchy/releases` | Omarchy releases |
| Community (optional) | `https://www.reddit.com/r/omarchy/.rss` | Reddit r/omarchy |
| Community (optional) | `https://hnrss.org/newest?q=Omarchy` | Hacker News |

Optional sources are labeled as community. A failure there does not fail the whole feed if official items arrived. DHH / Hey World RSS was not a usable feed (HTML interstitial), so it is not included — see opposition.

Normalized item:

```
{ id, title, lede, body_html?, body_text?, image_url?, source_name, source_url, published_at, fetched_at }
```

## Tests

```bash
python3 -m pytest tests/ -q
```

Network is not required. Parsers and cache behavior run against fixtures.

## License

MIT — SMF Works
