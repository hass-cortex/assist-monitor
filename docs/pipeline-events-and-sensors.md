# Pipeline event timeline → sensor updates

How one Home Assistant Assist voice conversation flows through the `assist_pipeline` event
sequence, and which Assist Monitor sensor picks up each event. Sources of truth:

- HA core `homeassistant/components/assist_pipeline/pipeline.py` (`PipelineEventType`, event
  payloads, `STORED_PIPELINE_RUNS = 10`)
- Official docs: [Assist pipelines](https://developers.home-assistant.io/docs/voice/pipelines/)
- This integration: `pipeline_reader.flatten_run()` (event → field), `coordinator.py`
  (field → view), `sensor.py` (view → sensor)

## 1. The event sequence of one voice run

A full voice-satellite run emits these `PipelineEvent`s (recorded in
`hass.data["assist_pipeline"].pipeline_debug`, last 10 runs per pipeline):

```mermaid
graph LR
    rs["run-start"] --> wws["wake_word-start"] --> wwe["wake_word-end"]
    wwe --> ss["stt-start"] --> vs["stt-vad-start"] --> ve["stt-vad-end"] --> se["stt-end"]
    se --> is["intent-start"] --> ip["intent-progress (0..n)"] --> ie["intent-end"]
    ie --> ts["tts-start"] --> te["tts-end"] --> re["run-end"]
```

A pipeline run can start at any stage (`start_stage`: `wake_word` / `stt` / `intent` / `tts`) and
stop early (`end_stage`), so not every run has every event. Per the official docs, only
`run-start`, `run-end`, `intent-start` and `intent-end` are always present; the wake/STT/TTS
events exist on audio runs only:

| Event | Present when | Absent when |
| --- | --- | --- |
| `wake_word-start/-end` | `start_stage = wake_word` (wake-word detection runs in the pipeline, e.g. openWakeWord streaming) | on-device wake word (satellite detects locally and starts at `stt`, passing `wake_word_phrase` only for the duplicate-wake-up cooldown — it reaches no event), push-to-talk, text Assist |
| `stt-vad-start/-end` | the pipeline's own `VoiceCommandSegmenter` runs: VAD enabled **and** the STT provider `requires_external_vad` | STT engines that do their own VAD/segmenting, text input |
| `intent-progress` | the agent streams deltas into the chat log (`chat_log_delta`), or streaming TTS kicks in mid-intent (`tts_start_streaming`) | local intents, one-shot agents |
| `tts-start/-end` | the reply is spoken (audio run reaching the TTS stage) | text runs (`end_stage = intent`), errors before TTS |
| `error` | any stage fails (skips the remaining stage events) | successful run |

An `error` event can occur at any point (a wake-word **timeout** raises `wake-word-timeout` and
goes through this path). `run-end` is emitted in a `finally` block (`PipelineInput.execute`), so
it **always** closes the run — after an error, and even after a silent wake-word abort (detection
ends with no result, e.g. the audio stream stops: such a run is `run-start` → `wake_word-start` →
`run-end` and never becomes visible to this integration; see section 4).

## 2. Event → parsed fields (`flatten_run`)

Each event contributes fields to the flattened run dict. The **data consumed** column lists only
the subset this integration reads — payloads carry more (full shapes in `pipeline.py` and the
official event table). `first_ts[type]` records the **first** occurrence's `PipelineEvent`
wall-clock ISO timestamp per event type; all durations are computed from those anchors
(section 3).

| Event | `data` consumed (full payload) | Fields written |
| --- | --- | --- |
| `run-start` | `conversation_id`, `satellite_id`, `pipeline`, `language` (also carries: `runner_data`, `tts_output` = pre-allocated stream `token`/`url`/`mime_type`/`stream_response`; the `satellite_id` key exists only on satellite runs — its absence drives `round_key`'s `x:` phase) | `conversation_id`, `satellite_id`, `pipeline_id`, `language`, `question_time` (= its timestamp) |
| `wake_word-end` | `wake_word_output.wake_word_phrase` / `.wake_word_id` (= `DetectionResult` minus `queued_audio`; also `timestamp`) | `wake_word` |
| `stt-start` | `engine` (also: `metadata`, `audio_processing`) | `stt_engine` |
| `stt-vad-start` / `stt-vad-end` | — (payload is a stream-relative ms `timestamp`; the reader uses the event's own wall-clock timestamp instead) | anchors for `speech_seconds` / `stt_seconds` / `latency_seconds` |
| `stt-end` | `stt_output.text` | `question` — **the run becomes publishable here** (`READY_MARKERS = {"stt-end", "error"}`) |
| `intent-start` | `engine`, `device_id` (also: `language`, `intent_input`, `conversation_id`, `satellite_id`, `prefer_local_intents`) | `engine`, `device_id` |
| `intent-progress` | `chat_log_delta`; the delta-less variant `tts_start_streaming: true` anchors `latency_seconds` | first delta with `content` / `thinking_content` / `tool_calls` sets the TTFT anchor; each `tool_calls` entry bumps `tool_calls` + `tool_names`; a `role: tool_result` delta that looks failed bumps `tool_error_count` + `failed_tools` |
| `intent-end` | `processed_locally`, `intent_output` (a [conversation response](https://developers.home-assistant.io/docs/intent_conversation_api#conversation-response)) | `status = success`, `processed_locally` → `mode` (`llm`/`local`) + `used_llm`, `continue_conversation`, `answer` (response speech), `response_type`; a successful local intent also counts as one tool call named `intent` |
| `tts-start` | `tts_input`, `engine`, `voice` (also: `language`, `acknowledge_override`) | `answer` fallback (if intent-end had no speech), `tts_engine`, `tts_voice` |
| `error` | `message`, `code` ([official error codes](https://developers.home-assistant.io/docs/voice/pipelines/#error-codes), e.g. `wake-word-timeout`, `stt-no-text-recognized`, `intent-failed`, `tts-failed`) | `status = error`, `error_message`, `error_code` |
| `run-end` | — (no data) | `finished = true` (terminal: nothing more can arrive for this run) |

The coordinator then annotates the view: `pipeline_id` → `pipeline_name`, and `device_id`
(fallback: the satellite's device, stable from `run-start`) → `device_name` + `area`. It also
merges `conversation_count` (bumped once per new `run_id`, persisted) and `round_key`.

## 3. Duration metrics — anchor map

```text
run-start   wake_word-start  wake_word-end  stt-vad-start  stt-vad-end  stt-end   intent-start   first token   intent-end  tts-start   tts-end    run-end
    |             |               |              |             |           |           |              |             |           |           |          |
    |             |<-wake_word_s->|              |<-speech_s-->|           |           |              |             |           |           |          |
    |                                                          |<--stt_s-->|           |<---ttft_s--->|             |           |           |          |
    |                                                          |           |           |<-------intent_seconds---->|            |           |          |
    |                                                          |<---------------latency_seconds------------------------------->|           |          |
    |                                                          |                                                               |<--tts_s-->|          |
    |<-------------------------------------------------------total_seconds------------------------------------------------------------------------->|
```

| Sensor | Start anchor | End anchor | Notes |
| --- | --- | --- | --- |
| `question_time` | `run-start` timestamp | — | timestamp sensor |
| `wake_word_seconds` | `wake_word-start` | `wake_word-end` | absent on push-to-talk |
| `speech_seconds` | `stt-vad-start` | `stt-vad-end` | length of spoken input; absent when the pipeline VAD is not running (section 1) |
| `stt_seconds` | `stt-vad-end` (fallback `stt-start`) | `stt-end` | matches the satellite's own STT metric |
| `intent_seconds` | `intent-start` | `intent-end` | agent/conversation time |
| `time_to_first_token` | `intent-start` | first `intent-progress` delta with any model output (text, thinking, or tool call) | non-streaming **successful** turns fall back to `intent-end` (= `intent_seconds`); a failed turn reports none. Pipeline-side, so it includes HA↔agent transport |
| `tts_seconds` | `tts-start` | `tts-end` | **stream-open/setup time only** — core emits `tts-end` immediately after handing the message to the TTS stream (`tts_output` = `media_id`/`token`/`url`), with no synthesis or playback wait |
| `latency_seconds` | `stt-vad-end` (fallback `stt-end`) | first `intent-progress` with `tts_start_streaming` (fallback `tts-start`) | user-perceived silence: stop speaking → assistant starts speaking. With a streaming agent **and** streaming-input TTS (`run-start` `tts_output.stream_response = true`), satellites (wyoming, esphome) start playback at the `tts_start_streaming` marker mid-intent — before `tts-start` — so that marker is the faithful speech-start anchor when present |
| `total_seconds` | `run-start` | `run-end` | whole run |

All anchors are the events' wall-clock ISO timestamps — the stream-relative millisecond
`timestamp` *inside* the `stt-vad-*` payloads is not used.

## 4. When sensors actually update

Events are read from the debug store, not pushed — the coordinator refreshes on:

1. **any `assist_satellite` state change** (idle → listening → processing → responding), giving an
   instant refresh at each stage boundary, and
2. a **1-second poll** as the universal safety net (covers non-satellite assists).

Updates are de-duplicated by value, so a sensor only fires a state change when its field actually
changed. A run is invisible until `stt-end` (or `error`) — wake-only / mid-STT runs, including a
silent wake-word abort, are skipped.

## 5. Stage-by-stage: what the sensors show

| Stage (pipeline progress) | Sensor changes on next refresh | `status` | `round_key` |
| --- | --- | --- | --- |
| wake word detected, user speaking | none — run not publishable yet | (previous run) | (previous round) |
| **`stt-end`** — question recognised | `last_question`, `assist`, `satellite`, `wake_word`, `language`, `stt_engine`, `question_time`, `wake_word_seconds`, `speech_seconds`, `stt_seconds`, `device`/`area` (from satellite), `conversations` +1 | `in_progress` | `q:<n>` |
| `intent-start` / `intent-progress` — agent working | `mode` attrs (`engine`), `device`/`area` (from `device_id`), `tool_calls` / `tool_errors` grow as deltas arrive | `in_progress` | `q:<n>` |
| **`intent-end`** — agent answered | `last_answer`, `mode`, `continue_conversation`, `intent_seconds`, `time_to_first_token` | `success` | `q:<n>` (deliberately: only speech or terminality flips the phase) |
| `tts-start` / `tts-end` — reply speaking | `tts_engine`, `tts_voice`, `tts_seconds`, `latency_seconds` | `success` | `a:<n>:<hash8>` (answer present) |
| **`run-end`** — run terminal | `total_seconds` | unchanged | `a:<n>:<hash8>`, or `e:<n>` if the run ended with no speech |
| `error` (any point) | `error_code`, `status` attrs (`error_message`) | `error` | `e:<n>` |

Notes on the last two columns:

- `status` is a **faithful mirror** of HA's events: `in_progress` until `intent-end`, and a run
  cancelled mid-flight (`run-end` without `intent-end`/`error`) **stays** `in_progress`, exactly as
  HA's own debug UI shows it. Terminality lives in the `finished` field and `round_key`'s `e:`
  phase instead — no derived status values, so a future HA terminal event surfaces unchanged.
- `round_key` is computed per refresh from the flattened run + conversation count
  (`pipeline_reader.round_key()`): `x:<n>` for non-satellite rounds, `e:<n>` for error **or**
  finished-without-speech, `a:<n>:<hash>` once an answer exists (hash changes if the answer is
  upgraded), else `q:<n>`. The intent-end → tts-start window deliberately stays `q:` so a normal
  streaming round never flashes an error phase mid-round. The sensor is registered after every
  other sensor, so on an ordered push stream it always emits last — see
  [Consuming a round race-free](sensors.md#consuming-a-round-race-free-round_key).

## 6. Edge cases

| Case | Behaviour |
| --- | --- |
| Push-to-talk / on-device wake word | `start_stage = stt`: no `wake_word-*` events → `wake_word` / `wake_word_seconds` are `None` (the phrase an on-device engine detected is never emitted by the pipeline) |
| Text Assist / HA app | `start_stage = intent`: no `satellite_id` → `round_key` = `x:<n>`; no STT/TTS events → timing sensors mostly `None`; the satellite-state listener never fires, so updates ride the 1 s poll |
| STT provider with built-in VAD | no `stt-vad-*` events → `speech_seconds` `None`, `stt_seconds` falls back to `stt-start`, `latency_seconds` falls back to `stt-end` |
| Silent wake-word abort (detection ends with no result, no timeout configured) | `run-start` → `wake_word-start` → `run-end` only; never publishable, sensors keep the previous round |
| Local intent (no LLM) | no `chat_log_delta` progress → TTFT falls back to `intent-end`; the matched intent counts as one tool call named `intent` |
| One-shot (non-streaming) agent | same TTFT fallback: `time_to_first_token` = `intent_seconds` on success |
| Streaming agent + streaming-input TTS | `intent-progress` `tts_start_streaming: true` marks playback starting mid-intent; `latency_seconds` ends there instead of `tts-start` (section 3) |
| Error before STT text (`stt-no-text-recognized`, `wake-word-timeout`, …) | published and counted (`READY_MARKERS` includes `error`) with an empty `last_question`; `round_key` = `e:<n>` so consumers see the round end |
| Cancelled run (`run-end` without `intent-end`/`error`) | `status` stays `in_progress` (faithful mirror); `finished` = `true`; `round_key` = `e:<n>` so consumers stop waiting |
| Silent success (finished, no speech) | `round_key` = `e:<n>` — terminal, no answer can arrive anymore |
| Failed tool calls | best-effort detection on the `tool_result` delta (`MCP error`, `isError`, `success:false` markers) → `tool_errors` + `failed_tools` |

## References

- HA core: `homeassistant/components/assist_pipeline/pipeline.py` — `PipelineEventType`, event
  payload emission sites, `PipelineInput.execute` (error handling, `finally: run.end()`)
- Official docs: [Assist pipelines — events & error codes](https://developers.home-assistant.io/docs/voice/pipelines/#events)
  (note: the official event table lags core slightly — e.g. `run-start` also carries
  `conversation_id`/`satellite_id`, and `wake_word-start` carries `entity_id` rather than `engine`)
- Verified against core master (2026-07) and this integration's `pipeline_reader.py` /
  `coordinator.py` / `sensor.py`
