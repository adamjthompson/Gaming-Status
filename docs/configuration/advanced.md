# Advanced
Update your API keys and fine-tune the game-matching engine.

* **API Keys & Tokens:** Update your SteamGridDB API key, or your Discord Bot Token and Server ID.
* **Steam Web API Key Override / PSN NPSSO Override:** *(Optional)* Only needed if the Steam / PlayStation Network integration isn't installed, or you want to use a different credential than the one already configured there. Leave blank to auto-detect. These are fallbacks: while the official integration supplies its own credential, that one is used instead. A new PSN NPSSO is checked with PlayStation when you save, and the form shows an error if it's rejected. The PSN override is used by native ratings, achievement tracking and library scans.
* **Same-Game Prefix Match:** Game names starting with this many matching leading words (e.g. "Call of Duty ...") are treated as the same game instead of a switch. Default: 2. Set to 0 to disable.
