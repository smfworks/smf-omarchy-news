# SMF Omarchy News — agent notes

For Hermes/OpenClaw installing this repo on a user's machine.

## Do

1. Clone to `$HOME/.hermes/plugins/smf-omarchy-news` (or `hermes plugins install smfworks/smf-omarchy-news --enable`).
2. Run `bash install.sh` from that tree. It enables the plugin on `$HOME/.hermes` **and** every `profiles/*/plugins` home, then copies `desktop/plugin.js` to `$HOME/.hermes/desktop-plugins/smf-omarchy-news/`.
3. Tell the user to **quit Hermes Desktop and relaunch from the menu**. The Python API (`plugin_api.py`) mounts only on the next `hermes serve`.

## Do not

- Do not treat ⌘K → Reload desktop plugins as a backend remount. That is JS only. **Backend not reachable** means the serve process predates enable — the pane cannot invent headlines to fill the gap.
- Do not run `hermes desktop` to relaunch if a packaged Electron binary already exists (`…/linux-unpacked/Hermes --no-sandbox`). `hermes desktop` rewrites the `.desktop` `Exec=` and can prompt for `chrome-sandbox` sudo.
- Do not `hermes serve --stop` (kills every serve on the box). Do not kill this chat's backend from inside the same Desktop window unless the user asked for a relaunch.
- Do not invent headlines, ledes, or article prose. A failed refresh must serve disk cache with `stale: true` / age, or `ok: false` + `errors[]` if there is no cache. Empty successful reads stay `ok: true`.
- Do not `git reset --hard` an existing plugin checkout.

## After relaunch

Sidebar **Omarchy News**, the right-of-chat pane, or ⌘K → Open Omarchy News. Optional status-bar chip shows a **new** count only after a successful feed and only for items published after the last time the user opened the pane — never a guessed number. Click a card to read in the same column; Back returns to the feed.
