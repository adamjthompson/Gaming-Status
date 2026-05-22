## 🔔 Notifications

The integration features a powerful, built-in notification engine. You can automatically broadcast when specific players start or finish gaming sessions directly to Discord, via SMS, or straight to your Home Assistant Companion App, without needing external tools like Node-RED or complex YAML automations.

The engine uses native Home Assistant memory to prevent duplicate alerts during rapid game-switching and gives the backend time to fetch high-resolution hero art before broadcasting.

**Example Discord Notifications**
![Discord Screenshot](../images/discord.png)

### ⚠️ Prerequisites
This feature leverages Home Assistant's native notification engine. **It does not create the connection to Discord or your SMS provider for you.** Before configuring this feature, you must already have your desired notification service set up and working in Home Assistant. 
* **For Discord:** You must install and configure the official [Discord Integration](https://www.home-assistant.io/integrations/discord/).
* **For SMS:** You must install an SMS integration (such as [Twilio](https://www.home-assistant.io/integrations/twilio/) or [ClickSend](https://www.home-assistant.io/integrations/clicksend/)).
* **For Mobile:** You must have the Home Assistant Companion App installed on your iOS or Android device.

Once your service is working and you can successfully call it from the Home Assistant Developer Tools, you can plug that exact service name into the Gaming Status integration!

### How to Configure Notifications

With Gaming Status, you can set up global notification destinations (like a specific phone or Discord channel) and assign them to individual players. 

**Step 1: Create a Notification**
1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click the **Configure** button on the Gaming Status card.
3. Select **Notifications** from the main menu and click Submit.
4. Select **➕ Add New Notification** and click Submit. 
5. Fill out the notification settings (see below) and submit to save. You can add as many methods as you need.

**Step 2: Assign to a Player**
1. From the main Gaming Status configuration menu, select **Manage Players**.
2. Select the player you want to edit and proceed to their **Player Details** screen.
3. Under **Session Start Notifications** and **Session End Notifications**, simply select the endpoints you created from the dropdown lists.
4. Click Submit. *(Home Assistant will automatically reload the integration in the background to apply your new routes!)*

### Destination Settings

* **Name:** A friendly identifier for this notification method (e.g., "Adam's iPhone" or "Family Discord").
* **Notification Type:** * `Discord`: Sends a rich embed message complete with the game's title, session duration, and the selected artwork.
    * `Mobile App`: Sends a Rich Notification directly to your iOS/Android Home Assistant app, featuring the game's art directly on your lock screen!
    * `SMS`: Sends a clean, text-only alert suitable for standard mobile text messages.
* **Notifier:** A dynamic dropdown containing all available Home Assistant notify services (e.g., `notify.mobile_app_adams_iphone`, `notify.discord_bot`, or `notify.twilio`).
* **Target ID:** The routing ID for the message. 
    * *For Discord:* Paste a Server Channel ID to post publicly to a group, or a specific User ID to send a private Direct Message.
    * *For SMS:* Paste the target phone number.
    * *For Mobile App:* Mobile notifications are routed entirely by the Notifier service name, so **you can leave the Target ID box blank**.