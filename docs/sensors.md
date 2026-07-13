# Devices & Sensors

## Devices

- **Assist Monitor (Latest)** — one device showing the most recent conversation across **all**
  assists (`sensor.assist_monitor_latest_*`). `…_satellite` tells you which voice satellite (if any)
  it came from.
- **Assist: &lt;name&gt;** — one device **per Assist pipeline** (the assistants under Settings → Voice
  assistants, e.g. *Pi*, *Claude Voice*, *Google*), named after it and tracking *that* assist's own
  latest conversation. These devices are **added and removed automatically** as pipelines are created
  or deleted.

Every device carries the same set of sensors (entity-id prefix differs per device).

## Sensor reference

| Sensor (key) | Value | Notes |
| --- | --- | --- |
| `last_question` | recognised speech | attrs: `conversation_id`, `satellite_id` |
| `last_answer` | assistant reply | state truncated to 255 chars; **full reply in the `full_text` attribute**, plus `response_type` |
| `mode` | `llm` / `local` | from `processed_locally` — a reliable LLM-vs-local flag (attrs: `used_llm`, `engine`) |
| `status` | `in_progress` / `success` / `error` | faithful mirror of HA's pipeline events: `in_progress` until intent-end — a cancelled run stays `in_progress` (HA emits no terminal event for it; check `round_key`'s `e:` phase for terminality) |
| `assist` | which assist (pipeline) handled it, e.g. `Pi` | attr `pipeline_id`; useful on the Latest device |
| `satellite` | which voice satellite (if any), as its raw `assist_satellite.*` entity id | diagnostic, **disabled by default** — human-facing equivalents are `device` / `area`, and `satellite_id` is also a `last_question` attribute |
| `wake_word` | the wake word that triggered it | e.g. `Okay Nabu`; only when wake-word detection runs **in the pipeline** (HA-side streaming, e.g. openWakeWord) — on-device wake word (e.g. microWakeWord) never reaches the pipeline events, so this stays empty |
| `continue_conversation` | whether the assistant expects a follow-up | |
| `tool_calls` | number of LLM tool invocations | attr `tools` lists their names; also `errors`, `failed_tools` |
| `tool_errors` | how many tool calls failed this turn | best-effort from the tool-result delta; attr `failed_tools` (diagnostic) |
| `conversations` | running count of conversations | `total_increasing` → daily totals / trends in HA statistics |
| `device` / `area` | the device and area that triggered it | diagnostic |
| `language` / `stt_engine` / `tts_engine` / `tts_voice` / `error_code` | per-run engine/language details | diagnostic |
| `question_time` | timestamp of the turn | |
| `wake_word_seconds` | wake-word detection time | `wake_word-start` → `wake_word-end`; absent on push-to-talk and on-device wake-word runs |
| `stt_seconds` | speech-to-text time | |
| `speech_seconds` | length of spoken input | |
| `intent_seconds` | agent/conversation time | |
| `time_to_first_token` | `intent-start` → first streamed token (text, thinking, or tool call) | pipeline-side / HA-observed, so it includes the HA↔agent transport; a few hundred ms larger than an agent's own internal TTFT. Non-streaming turns (local intents, or any agent that answers in one shot) have no streamed token, so a **successful** one falls back to `intent-end` (= `intent_seconds`); a failed turn reports none |
| `tts_seconds` | text-to-speech **setup** time only (stream-open) — not synthesis/playback | |
| `latency_seconds` | **user-perceived silence**: end of speech (VAD end) → assistant starts speaking | ends at the early-streaming marker (`tts_start_streaming`) when streaming TTS kicks in mid-turn, else at `tts-start`; the single number that matters most for voice latency |
| `total_seconds` | whole-run time | |
| `round_key` | `<phase>:<n>[:<hash8>]` — semantic round key for machine consumers | diagnostic, **disabled by default** (enable in the entity registry); see [below](#consuming-a-round-race-free-round_key) |

Exact event-to-sensor anchors and the per-stage update timeline are documented in
[Pipeline events & sensor updates](pipeline-events-and-sensors.md).

## Real-time / incremental behaviour

- After **speech-to-text** finishes → `last_question` updates and `status` = `in_progress`.
- After the **agent** answers → `last_answer`, `mode`, timings update; `status` = `success`.
- The answer is published **once** (not streamed token-by-token), which suits slow displays such as
  e-paper.

## Consuming a round race-free (`round_key`)

A device (e-paper display, ESPHome node, …) that renders one conversation round from several of
these sensors would normally have to poll them all and guess when the set is consistent. The
`round_key` sensor removes that race: it is registered **after every other sensor**, so on an
ordered per-entity push stream (e.g. Home Assistant → ESPHome) it always changes **last** — when it
changes, every other field of the round is already delivered. Subscribe to `round_key` alone,
re-read the rest on change.

Its value is `<phase>:<n>[:<answer_hash>]`, where `n` is the cumulative conversation number (the
round discriminator):

| Phase | Meaning |
| --- | --- |
| `q:<n>` | question recognised, no answer yet |
| `a:<n>:<hash8>` | answer present (hash changes if the answer is upgraded) |
| `e:<n>` | terminal without speech — error (even before a question exists), cancelled, or finished silently (stop waiting) |
| `x:<n>` | non-satellite round (HA app / text Assist) — a voice display skips these |

It is a diagnostic sensor and **disabled by default** — enable it in the entity registry if you
need it.
