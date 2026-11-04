# Changelog

All notable changes to Gaming Status are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **Better Xbox artwork now shows up right away.** The background artwork search swaps Xbox's own cover images for higher-quality SteamGridDB artwork. That artwork wasn't reaching dashboards until the next full library scan, up to 24 hours later depending on your scan interval. Home Assistant saw no change, so it never sent an update. It now appears as soon as the background search finds it.

### Performance

These changes speed things up without changing what Gaming Status shows or does.

- **PlayStation trophy checks make fewer requests.** A game's trophy list (names, descriptions, icons and tiers) is the same for every player, so it is now cached for 24 hours and shared across players. Each trophy recheck now only downloads your progress. This removes about a third of the PlayStation requests made while playing and during library backfills, leaving more of the shared PSN rate limit for everything else. Trophies added by DLC still appear within a day.
- **The public status page uses less CPU.** The page's data was already cached for 30 seconds, but it was converted to JSON again for every visitor on every refresh. It is now converted once each time the cache is rebuilt and reused for every visitor. Large game libraries benefit the most.
- **Library scans finish sooner.** Steam, Xbox and PlayStation libraries are now scanned at the same time instead of one after another, so a full scan takes about as long as the slowest platform. Background artwork backfill, which pauses during scans, resumes sooner as a result. If a game you own on more than one platform needs artwork, it is still only looked up once.
- **Searching for missing cover art uses less disk access.** Looking for a game's artwork in the local cache used to check the disk separately for every image type and file extension, up to 36 times. It now reads the cache folder once and picks the same files as before.
