# Custom Artwork & Colors
Manually assign artwork or colors to specific games using simple `Game = Value` lists.

* **Custom Asset Maps (Grid, Hero, Logo, Icon):** Assign artwork for custom games or unrecognized titles. Fully supports web URLs and Home Assistant `/local/` paths. *(Example: `Marvel Rivals = /local/covers/marvel_rivals.png`)* A matching title uses the provided URL immediately and bypasses the SteamGridDB lookup entirely, which is useful for obscure games, custom emulators, or fan art you prefer over the official artwork. URLs must point directly to an image file (e.g., .png, .jpg). Any URL can be used, including local files, but if the file is not publicly available, it will not display in notifications.
    ```
    Marvel Rivals = https://cdn2.steamgriddb.com/hero/a31d2779e08530d0b5fdbed368c735b4.png
    Halo = /local/halo.png
    ```
* **Custom Colors:** Override the automatic dominant color extractor by assigning specific hex codes to games. *(Example: `Cyberpunk 2077 = #fcee0a`)*
