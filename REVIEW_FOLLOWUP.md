# Review follow-up: low-impact fixes still to discuss

This is a handoff note for continuing the November 2026 performance and security review, on this computer or another one. To pick up where we left off, open this repo in Claude Code and say: *"Read REVIEW_FOLLOWUP.md and let's go through the low-impact items."*

This file is a working note, not project documentation. Delete it once the items are resolved.

## Where things stand (2026-11-04)

The review covered the whole integration for performance and security changes that don't change functionality. It found no high-impact issues; commit `3a77cf0` had already fixed the main risks.

**Done, but not committed or tested in Home Assistant.** All of these are listed under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).

| Change | File |
|---|---|
| PlayStation trophy metadata cached for 24 hours and shared across players | `psn_client.py` |
| Public page JSON serialized once per cache rebuild instead of once per visitor | `public_page.py` |
| Steam, Xbox and PlayStation library scans run at the same time, with duplicate artwork lookups merged | `library_scan.py` |
| Local art search uses one directory listing instead of up to 36 file checks | `sensor.py` |
| **Bug fix:** background Xbox artwork now reaches dashboards right away instead of at the next full scan | `library_scan.py` (`_backfill_art`) |

> **Moving between computers:** these edits aren't committed, so they only reach the other computer through OneDrive sync, not git. Check that `git status` there shows the changed files above, including `CHANGELOG.md` and this file. Ruff wasn't installed on the Mac where these were made, so `ruff check` hasn't been run against them.
>
> If `git status` shows dozens of files changed, it's the OneDrive file-permission problem from earlier. Run `git config core.fileMode false` in the repo on that computer too.

## Low-impact items still to decide

They're ranked by impact, highest first. Line numbers are as of the date above. Each change is meant to leave behavior unchanged; any exceptions are noted.

### Security

**S1. The password-protected public page can be stored by shared caches**
- **Where:** `public_page.py:975`, in `GamingStatusPublicPageView`.
- **Problem:** the page's HTML includes the page key in its preview tags and links, but it's sent with `Cache-Control: no-cache`. That setting still lets a proxy or CDN store the page. The JSON view already uses `private`.
- **Fix:** send `"private, no-cache"` when `page_key(entry)` is set, and leave it unchanged otherwise.
- **Effort:** one line.

**S2. SVG cover art is accepted**
- **Where:** `image.py:106`.
- **Problem:** `content_type.startswith("image/")` lets `image/svg+xml` through. Home Assistant then serves it from its own address through `/api/image_proxy`. An SVG opened directly can run script with access to the logged-in Home Assistant session.
- **Fix:** allow only png, jpeg, webp, gif, avif and bmp, or simply reject SVG.
- **Why behavior is unchanged:** none of the artwork sources (SteamGridDB, Steam, Xbox, PSN) serve SVG.

**S3. Discord bot token is kept in memory as a lookup key, and the server ID isn't checked**
- **Where:** `config_flow.py:120` and `config_flow.py:125`, in `_fetch_discord_members`.
- **Problem:** `cache_key = (token, server_id)` keeps every bot token ever entered in memory until Home Assistant restarts. `server_id` is also put into the request URL without validation.
- **Fix:** key the cache on `hashlib.sha256(token.encode()).hexdigest()`, and return `[]` if `not server_id.isdigit()`.
- **Why behavior is unchanged:** Discord server IDs are always numeric.

**S4. The Xbox library has no upper version limit**
- **Where:** `manifest.json:14`, which says `"python-xbox>=0.2.0"`.
- **Problem:** a future major release would install automatically. `nextcord` already has an upper limit.
- **Fix:** add `,<1.0`. First confirm the version currently installed is below 1.0.

**S5. The page preview links trust the browser's `Host` header**
- **Where:** `public_page.py:953`, which uses `origin = str(request.url.origin())`.
- **Problem:** the value is escaped, so there's no script injection. A misconfigured shared cache could still store previews pointing at another domain. S1 covers most of this.
- **Fix:** use `get_url(hass, prefer_external=True)` when an external URL is configured, and fall back to the request origin otherwise.

### Performance

**P1. Background work keeps running after a reload**
- **Where:** `library_scan.py:367` and `__init__.py:430` (`_library_backfill_tick`).
- **Problem 1:** the handle from `async_call_later(...)` is thrown away, so the old coordinator's timer stays scheduled after an options reload.
- **Problem 2:** a backfill that's already running keeps making Xbox, PSN and SteamGridDB calls on the old, unused coordinators. It also saves to the same storage file the new coordinator uses.
- **Fix:**
  1. Store the handle in `self._unsub_resume` and cancel it in an `async_shutdown` override.
  2. Return early from `async_run_backfill_pass` once the coordinator has shut down, or once the entry is no longer loaded.

**P2. A debug log line does real work even when debug logging is off**
- **Where:** `library_scan.py:1432-1437`, the `if pending:` block in `_backfill_art`.
- **Problem:** the log line counts the remaining titles with a `sum(...)`, which is another full pass over up to about 4,000 Xbox titles. It runs every backfill tick, even with debug logging off.
- **Fix:** change the condition to `if pending and _LOGGER.isEnabledFor(logging.DEBUG):`.
- **Effort:** one line.

**P3. Xbox title history is parsed on Home Assistant's main loop**
- **Where:** `xbox_client.py:354-358`, which runs `json.loads(resp.text)` and then `TitleHubResponse.model_validate(data)`.
- **Problem:** for thousands of titles this is slow, but it only happens once per scan.
- **Fix:** move the parse and validation into one function and run it with `hass.async_add_executor_job`. That needs `hass` passed in, or the call moved to the caller in `utils`.

**P4. Artwork and rating lookups wait on each other when the game changes**
- **Where:** `sensor.py:4432` (`fetch_game_assets`) and `sensor.py:4525` (`fetch_game_rating`).
- **Problem:** they're independent network calls, but they run one after another.
- **Fix:** run them together with `asyncio.gather`, keeping each block's existing `current_game_normalized` guard.
- **Constraint:** achievements must stay after artwork, because `_ingest_recent_unlocks` uses the cached hero image and colour.

**P5. Fixed-format dates go through a general-purpose date parser**
- **Where:** `sensor.py:5387`, plus lines 872, 897, 914 and 2179.
- **Problem:** `dateutil.parser.parse` is used on fixed `YYYY-MM-DD` keys.
- **Fix:** add a helper that tries `date.fromisoformat(s)` and falls back to `parser.parse(s).date()` on `ValueError`.
- **Impact:** small. It's about 10–30 times faster per date, but only tens of dates are parsed each time.

## Ruled out (don't redo)

These were considered and left out on purpose:
- **DNS-rebinding hardening in `is_public_url`** (`utils.py:2205`): it would need a custom resolver, which is too invasive for a low-impact issue.
- **Skipping the trophy detail fetch on unchanged rechecks:** it would stop late icon and description updates on older trophies.
- **Adding `launcher` and `gsa_gamertag` to `_unrecorded_attributes`:** it would change what the recorder keeps in history.
- **Sending PSN's two trophy requests at the same time:** it would spend rate-limit budget even when the first request fails.
- **Running SteamGridDB endpoint calls in parallel:** the calls share one rate limiter, and requests sent after a 429 response would change error handling.

## How to check the finished changes

On a dev Home Assistant instance:
- PlayStation achievements still update after a 15-minute recheck.
- The public page loads, with and without a key. Use `curl -I` to check its headers.
- A manual library scan gives the same totals as before.
- Local cover art still appears.
- After the background artwork search logs "N updated", an Xbox game's `game_cover_art` changes right away in Developer Tools → States.
