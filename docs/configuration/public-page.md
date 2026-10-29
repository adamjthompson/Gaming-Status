# Public Page
An optional page that anyone with the link can view **without logging in**, at `/gaming_status/public` on your Home Assistant address (e.g. `http://<your-ha-ip>:8123/gaming_status/public`). It uses the same cards as [Gaming Status Cards](https://github.com/adamjthompson/Gaming-Status-Cards), so it looks like your own dashboards, and refreshes about once a minute, showing when it was last updated at the bottom. Sharing the link in Discord, iMessage, Slack and similar apps shows a preview with the page title, icon, and how many players are online. Gaming Status Cards doesn't need to be installed for the built-in layout: if it isn't, the page loads the latest public release of the cards. You do need it to design a mirrored dashboard view. The settings screen shows the page's full link.

The page returns "Not found" until **Enable public page** is on *and* at least one player has **Show on public page** checked (Manage Players → Player Details). Only those players appear, and the page's "players online" counts include only them.

* **Enable public page:** Serves the page. Your Home Assistant must be reachable from the internet for people outside your network to see it (see [Sharing the page outside your network](public-page.md#sharing-the-page-outside-your-network)).
* **Require private link:** Off by default. When on, the page's link gets a secret `?key=...` added, and anyone opening the plain address gets "Not found". Share the full link shown at the top of the settings screen.
* **Regenerate private link key:** Creates a new key on save. Every link shared with the old key stops working.
* **Page title:** Shown as the page heading and browser tab title. Default: Gaming Status.
* **Dashboard view to mirror:** Design the page on any dashboard view with the cards' own editors, and the public page shows the Gaming Status cards from that view with the same settings and grouping (sections and stacks stay together). Other card types are skipped, as are cards pinned to a player who isn't public. A dedicated dashboard hidden from the sidebar works well. Leave it on **Built-in default layout** to have the page laid out automatically instead.
* **Default layout: general cards / cards for each player:** *(Built-in default layout only)* Which cards to show once for everyone (e.g. player list, slideshow, leaderboard, weekly stats) and which to show grouped under each public player (e.g. Gamercard, game library, 100% completed games, near completion). Cards appear in the order listed; to reorder, remove a card and add it back to move it to the end. A per-player card is skipped for a player who doesn't have the data it needs, and the Gamercard is shown once per platform the player has a library on.
* **Exclude libraries with zero tracked achievements:** *(Built-in default layout only)* On by default. Game library cards leave out games with no achievements earned, and are skipped (or hide a platform's tab) when a player has none at all.
* **Light or dark mode:** **Auto** follows each visitor's device; **Light** or **Dark** forces it for everyone.
* **Theme:** An installed Home Assistant theme for the page. The default uses the mirrored view's own theme if it sets one, otherwise Home Assistant's default colors.
* **Show gamertags:** Off by default. When off, gamertags are removed before any data leaves Home Assistant. When on, the built-in layout shows them in the player list and Gamercards; on a mirrored view, also turn on **Show gamertags** in each card's editor.
* **Cards script URL:** *(Optional)* Leave blank to use the Gaming Status Cards resource already installed in your dashboards; falls back to the latest public release if none is found.

**What's never shared:** Credentials, account IDs, parental control data, and (unless enabled) gamertags are stripped from the page's data. The Game Management card is never shown, and the page can't call any services or change anything. Each visitor's address is limited to 120 requests a minute, which is well above what an open page uses.

## Sharing the page outside your network
If you put the page behind a reverse proxy or tunnel (e.g. Cloudflare Tunnel) and only want to expose the page rather than all of Home Assistant, allow these paths:

| Path | Why |
| --- | --- |
| `/gaming_status/public` | The page itself |
| `/api/gaming_status/public.json` | The data the page loads |
| Your Gaming Status Cards resource, e.g. `/hacsfiles/Gaming-Status-Cards/` | The cards script (match the path and capitalization under Settings → Dashboards → Resources), unless you set **Cards script URL** to a public copy |
| `/local/gaming_status_cache/` | Cached game artwork |
| `/api/image_proxy/` | Xbox and PlayStation gamerpics |

Add any other `/local/` folders that hold images (such as `/local/gaming_status/`) you've set as overrides. Home Assistant also needs `use_x_forwarded_for` and `trusted_proxies` set under `http:` in `configuration.yaml` for proxied requests to be accepted.
