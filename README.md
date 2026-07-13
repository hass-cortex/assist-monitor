# Assist Monitor for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/hass-cortex/assist-monitor)](https://github.com/hass-cortex/assist-monitor/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.3.0+-green.svg)](https://www.home-assistant.io/)
[![GitHub License](https://img.shields.io/github/license/hass-cortex/assist-monitor)](https://github.com/hass-cortex/assist-monitor/blob/main/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hass-cortex/assist-monitor)

A Home Assistant custom integration that exposes the **latest Assist conversation** — across
**every** assist / pipeline / voice satellite — as plain sensors, **updated incrementally in real
time** (the question appears as soon as speech-to-text finishes; the answer and timings fill in as
the run progresses).

```
Assist pipeline runs (assist_pipeline in-memory debug store)
        │
        │  5 s safety-net poll + debounced instant refresh on assist_satellite state changes
        ▼
"Latest" device + one device per Assist pipeline
(question / answer / mode / status / tool calls / timings / round_key)
```

Home Assistant does not expose per-conversation Assist data as entities, and the pipeline events are
not on the event bus. The usual workaround is to have the voice-satellite device republish the data.
This integration instead reads it **directly from the `assist_pipeline` in-memory debug store on the
HA side**, so it works for any assist and needs no device cooperation.

## Features

- **Real-time incremental updates** — the question appears at speech-to-text end; answer, mode, and
  timings fill in as the run progresses; the answer is published once (not token-by-token), which
  suits slow displays such as e-paper
- **Every assist covered** — voice satellites and non-satellite assists (HA app, text Assist) alike;
  no device cooperation required
- **One device per assist** plus a global **Latest** device — per-pipeline devices are added and
  removed automatically as Assist pipelines change ([details](docs/sensors.md))
- **Full conversation view** — question/answer, LLM-vs-local mode, status, tool calls and failures,
  wake word, satellite, triggering device/area
- **Voice latency breakdown** — wake word, STT, agent, TTS setup, time-to-first-token, and
  user-perceived silence (`latency_seconds`), anchored to the actual pipeline events
  ([details](docs/pipeline-events-and-sensors.md))
- **Conversation counters** — persisted `total_increasing` counts per assist for daily totals and
  trends in HA statistics
- **Race-free machine consumption** — an opt-in `round_key` ordering-sentinel sensor lets
  e-paper/ESPHome displays aggregate a whole round from a single state change
  ([details](docs/sensors.md#consuming-a-round-race-free-round_key))

## Getting Started

**Prerequisites:** Home Assistant **2026.3.0+**. No accounts, tokens, or configuration needed.

### 1. Install

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hass-cortex&repository=assist-monitor&category=integration)

Click the button above, or manually: HACS > three-dot menu > **Custom repositories** > add
`https://github.com/hass-cortex/assist-monitor` (Integration) > install > restart HA.

<details>
<summary>Manual installation</summary>

Copy `custom_components/assist_monitor/` to your HA `config/custom_components/` directory, then restart.
</details>

### 2. Add Integration

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=assist_monitor)

Click the button above, or manually: **Settings > Devices & Services > Add Integration** > search
"Assist Monitor". Single instance, zero configuration.

### 3. Use the Sensors

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=assist_monitor)

Speak to any assist — the **Assist Monitor (Latest)** device and the per-assist devices update as
the conversation progresses. See the [sensor reference](docs/sensors.md) for every entity.

### Configuration Options

There are none — the integration is deliberately zero-config. The only opt-in is the `round_key`
sensor (disabled by default): enable it in the entity registry if you drive an e-paper/ESPHome
display ([why and how](docs/sensors.md#consuming-a-round-race-free-round_key)).

### Uninstallation

**Settings > Devices & Services** > Assist Monitor > three-dot menu > **Delete** > remove
`custom_components/assist_monitor/` (or uninstall via HACS) > restart HA. The persisted
conversation counters live in `.storage/assist_monitor_counts` and can be deleted afterwards.

## Debugging

The sensors mirror what Home Assistant's own Assist debug view shows — if a value looks wrong,
compare it against **Settings → Voice assistants → (assist) → three-dot menu → Debug**, which
renders the same last-10-runs store this integration reads. The exact event-to-sensor mapping is
documented in [docs/pipeline-events-and-sensors.md](docs/pipeline-events-and-sensors.md).

## FAQ

**Will this break on a Home Assistant update?**

It reads an **internal** `assist_pipeline` structure (`pipeline_debug`), which is not a stable
public API and could change between HA releases. Access is read-only and resilient to the
structure's absence — worst case the sensors go stale, nothing else is affected. The event mapping
is verified against HA core in [docs/pipeline-events-and-sensors.md](docs/pipeline-events-and-sensors.md).

**Why does `status` stay `in_progress` after a cancelled conversation?**

Because HA emits no terminal event for a cancelled run, and `status` is a faithful 1:1 mirror of
HA's pipeline events — the integration never invents values. Terminality is available separately:
the `round_key` sensor flips to its `e:` phase when a run ends without speech.

**Does it keep a conversation history?**

Yes, through standard HA mechanisms. Each device *shows* only its most-recent conversation, but the
sensors are ordinary entities — HA's recorder logs every state change, so the history of
`last_question` / `last_answer` (and the timing sensors) is a browsable conversation log for as
long as your recorder retention (`purge_keep_days`, default 10 days). The `conversations` counter
additionally feeds HA long-term statistics for daily totals and trends beyond that window.

**How do I drive an e-paper / ESPHome display without races?**

Enable the `round_key` sensor and subscribe to it alone — it always changes after every other
sensor of the round, so when it fires the full round is already consistent. Phases tell you what to
render: `q:` question, `a:` answer, `e:` round over without speech, `x:` non-voice round. See
[docs/sensors.md](docs/sensors.md#consuming-a-round-race-free-round_key).

**Why is `latency_seconds` larger than my agent's own latency metric?**

It measures the **user's** experience — end of speech to the assistant starting to speak — on the
HA side, so it includes STT finalisation, HA↔agent transport, and TTS setup. Same for
`time_to_first_token`, which is HA-observed rather than agent-internal. Anchors are documented in
[docs/pipeline-events-and-sensors.md](docs/pipeline-events-and-sensors.md).

**How do I install the latest development version?**

After the integration is installed via HACS, switch to the latest `main` branch using the
`update.install` action:

1. Go to **Developer Tools > Actions**
2. Select the `update.install` action
3. In **Target**, select the Assist Monitor update entity (e.g., `update.assist_monitor_update`)
4. In **Version**, enter `main` (or a specific commit hash)
5. Click **Perform Action**
6. Restart HA

Development versions may contain breaking changes — to revert, run the same action with a release
tag (e.g., `0.1.0`).

## Documentation

| Document | Description |
|----------|-------------|
| [Devices & Sensors](docs/sensors.md) | Every device and sensor, plus race-free `round_key` consumption |
| [Pipeline events & sensor updates](docs/pipeline-events-and-sensors.md) | Event timeline, timing-metric anchors, per-stage updates — verified against HA core |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and contribution guidelines.

## License

[MIT](LICENSE)
