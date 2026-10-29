# Voice assistants (Assist)
Gaming Status adds three questions you can ask Home Assistant's Assist:

| Question | Example answer |
| --- | --- |
| Who's playing games? | "Adam is playing ELDEN RING on Steam, and Josh is playing Halo on Xbox." |
| What is *player* playing? | "Adam is playing ELDEN RING on Steam." or "Adam isn't playing anything right now. The last game was Halo." |
| How long has *player* played? | "Adam has played 2 hours and 15 minutes today and 9 hours this week." |

**AI-powered assistants** (OpenAI, Google Gemini, Anthropic Claude, Ollama and others set to control Home Assistant): these work automatically, once at least one player's master sensor is exposed to the assistant (**Settings → Voice assistants → Expose**). Ask in your own words.

**Home Assistant's built-in assistant** only understands sentences it has been taught. Create `custom_sentences/en/gaming_status.yaml` in your Home Assistant config folder with the following, then restart:

```yaml
language: "en"
intents:
  GamingStatusWhoIsPlaying:
    data:
      - sentences:
          - "who is (playing [video] games|gaming)"
          - "who's (playing [video] games|gaming)"
          - "is anyone (playing [video] games|gaming)"
  GamingStatusWhatIsPlaying:
    data:
      - sentences:
          - "what is {player} playing"
          - "what's {player} playing"
          - "what game is {player} playing"
  GamingStatusPlayTime:
    data:
      - sentences:
          - "how long has {player} (played|been playing|been gaming) [today|this week]"
          - "how much has {player} played [today|this week]"
lists:
  player:
    wildcard: true
```

Player names are matched without regard to capitalization, so "what is adam playing" works for a player named Adam.
