# Device Page Settings
Common settings can also be changed from each player's device page (**Configuration** section), from a dashboard, or in automations, without opening Configure. These controls read and write the same settings as Configure, so a change in either place shows up in the other.

**Per player**

| Setting | Control | Applies |
| --- | --- | --- |
| Screen time limit | On/off | Immediately |
| Weekday screen time, Weekend screen time | Minutes | Immediately |
| Screen time warning | Slider, minutes before the limit (0 = off). Fires the Screen time warning event | Immediately |
| Screen time reminder interval | Once per day, or every 15, 30 or 60 minutes | Immediately |
| Curfew | On/off | Immediately |
| Weekday curfew, Weekend curfew | Time | Immediately |
| Curfew reminder interval | Once per day, or every 15, 30 or 60 minutes | Immediately |
| Rating limit, Maximum age rating | On/off, and Everyone through Adults Only. Only while **Native Content Ratings** is on | Immediately |
| Steam / Xbox / Epic / Ubisoft achievement tracking, PlayStation trophy tracking | On/off, one per platform the player has (Epic once the player has an Epic account set, Ubisoft once they have a Gaming Status Agent sensor). Only while **Achievement/Trophy Tracking** is on | Reloads Gaming Status |
| Steam / Xbox / PlayStation / Epic / Ubisoft library scan | On/off, one per platform. Only while **Full Game Library Scan** is on | Reloads Gaming Status |
| Show on public page | On/off | Immediately |

**On the Gaming Status hub device:** Public page, Notifications, Parental controls and Weekly report on/off, all applied immediately.

**Parental controls follow their rules.** A player's parental controls only appear while they'd do something:

- **Screen time limit, Curfew and Rating limit** are available while **Parental controls** is on.
- **Each rule's other controls** (its times, minutes, warning and reminder interval) are available only while that rule is on.

Turning a rule on or off in Configure shows or hides its controls straight away. Turning it on or off from its own switch takes about 30 seconds, because Home Assistant briefly reloads Gaming Status to add or remove them. If you disable one of these controls yourself (for example, to keep it out of reach of other people in your home), Gaming Status leaves it disabled.

> **Who can change these:** Configure is limited to administrators. By default, anyone who can use your Home Assistant dashboards can also change an enabled parental control. If players have their own Home Assistant accounts, turn on **Restrict history edits and parental settings to admins** in Configure → Advanced Settings: non-admin users then get an error if they try to change a parental control (or the hub's Parental controls switch), while automations keep working.

Notification destinations, and the action each parental rule takes, are only set in Configure.
