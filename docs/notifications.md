## 🔔 Automated Notifications

The integration features a powerful, built-in notification engine. You can automatically broadcast when specific players start or finish gaming sessions directly to Discord, via SMS, or straight to your Home Assistant Companion App, without needing external tools like Node-RED or complex YAML automations.

The engine uses native Home Assistant memory to prevent duplicate alerts during rapid game-switching and gives the backend time to fetch high-resolution hero art before broadcasting.

**Example Discord Notifications**
![Discord Screenshot](../images/discord.png)

### ⚠️ Prerequisites
This feature leverages Home Assistant's native notification engine. **It does not create the connection to Discord or your SMS provider for you.** Before configuring this feature, you must already have your desired notification service set up and working in Home Assistant. 
* **For Discord:** You must install and configure the official [Discord Integration](https://www.home-assistant.io/integrations/discord/).
* **For SMS:** You must install an SMS integration (such as [Twilio](https://www.home-assistant.io/integrations/twilio/) or [ClickSend](https://www.home-assistant.io/integrations/clicksend/)).
* **For Mobile:** You must have the Home Assistant Companion App installed on your iOS or Android device.

Once your service is working and you can successfully call it from the Home Assistant Developer Tools, you can plug that exact service name into the Gaming Status configurator!

### How to Configure

1. Open the **Gaming Status** configurator from your Home Assistant sidebar.
2. Under any Player Profile, scroll down to the **Automated Notifications** section.
3. Toggle **Notify on Game Start** and/or **Notify on Game End** depending on what you want to track for that specific person.
4. Click **+ Add Destination** to configure where the alert should be sent. You can add multiple destinations per player.

### Destination Settings

* **Type:** * `discord`: Sends a rich embed message complete with the game's title, session duration, and the horizontal hero banner.
    * `mobile`: Sends a Rich Notification directly to your iOS/Android Home Assistant app, featuring the game's hero art directly on your lock screen!
    * `sms`: Sends a clean, text-only alert suitable for standard mobile text messages.
* **Service:** The exact name of your Home Assistant notify service (e.g., `notify.chat_bot`, `notify.mobile_app_adams_iphone`, or `notify.twilio`).
* **Target:** The routing ID for the message. 
    * *For Discord:* Paste a Server Channel ID to post publicly to a group, or a specific User ID to send a private Direct Message.
    * *For SMS:* Paste the target phone number.
    * *For Mobile App:* Mobile notifications are routed entirely by the Service name, so **you can leave the Target box blank** (or type `N/A`).

*Note: After adding or modifying notification routes in the UI, be sure to click "Save Configuration" and then reload the integration (Settings > Devices & Services > Gaming Status > Reload) for the new routes to take effect.*