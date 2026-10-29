# Parental Controls
Set automated rules based on accumulated playtime, time of day, or game content rating.

* **Enable Parental Controls:** Turns screen time limits, curfews, and rating rules on or off for every player. Each player's rules are kept while it's off.
* **Player settings:** Choose **Edit: <name>** to configure a player's rules, or leave it on **Save and return to main menu** to just save the toggles.
* **Enable Native Content Ratings:** Shown on the main Parental Controls screen, below the player picker — see [Content Ratings](../getting-started/prerequisites.md#content-ratings). This is what actually turns the rating pipeline on; Content Rating Limit below only appears on a player's screen once this is checked and saved, since there'd be nothing for the rule to compare against otherwise.
* **Screen Time:** Set distinct weekday and weekend daily minute limits.
* **Curfew:** Set distinct weekday and weekend cutoff times (e.g., 22:00).
* **Content Rating Limit:** Set a maximum allowed age rating (e.g., "Ages 13+"). Ratings are looked up automatically from Steam/Xbox/PlayStation's own native rating data and cached indefinitely once found for a game. If the player's active game rating exceeds the configured age, a notification is sent the moment that game starts — switching to a *different* over-the-limit game always sends a fresh notification, but the same game won't spam you again while it keeps running. The notification-method picker for this rule appears alongside **Enable content rating limit** as soon as **Enable Native Content Ratings** is enabled.
* **Reminder Frequency:** *(Screen Time and Curfew only)* Set how often to repeat the notification(s).
* **Notifications:** When a limit is reached, you can automatically send a notification using any of your configured methods.
