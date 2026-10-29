# Devices

Every player gets their own Home Assistant **Device** (Settings → Devices & Services → Gaming Status → Devices), named after the player and grouping *all* of their entities together — platform sensors, the Master and PC sub-master sensors, Game Library sensors, and their Game Library Refresh button all live under one device page, instead of a flat list of unrelated entities. The device's "model" field lists whichever platforms that player has configured (e.g. "Steam + Xbox").

The two entities that aren't tied to any single player — **Players Online** and **Anyone Gaming** — are grouped under a separate, shared **Gaming Status** hub device instead.

Deleting a player from Manage Players removes their entire device (and everything on it) in one go.
