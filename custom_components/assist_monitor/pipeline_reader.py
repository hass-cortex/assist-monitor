"""Read and parse the latest Assist pipeline run from assist_pipeline's in-memory debug store.

The assist_pipeline integration keeps the last STORED_PIPELINE_RUNS (10) runs per pipeline in
``hass.data["assist_pipeline"].pipeline_debug[pipeline_id][run_id].events`` (a list of
``PipelineEvent``). Each event has ``.type`` (a StrEnum like ``"stt-end"``), ``.data`` (dict | None)
and ``.timestamp`` (ISO string). We scan that store for the globally-newest COMPLETE run across every
pipeline / assist and flatten it into a plain dict of the fields a display cares about.

``flatten_run`` is intentionally pure (no Home Assistant imports) so it can be unit-tested with plain
event objects; ``parse_all`` is the thin Home Assistant wrapper.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# In-memory store key used by homeassistant.components.assist_pipeline
# (KEY_ASSIST_PIPELINE = HassKey(DOMAIN)); holds PipelineData with .pipeline_debug. Defined here
# (not imported from .const) so this module stays free of package-relative imports and can be
# unit-tested standalone.
ASSIST_PIPELINE_KEY = "assist_pipeline"

# One-shot flag: the debug store exists but has an unrecognized shape (internal layout
# changed). Warn once instead of silently publishing empty views forever.
_warned_unrecognized_store = False

# A run becomes publishable as soon as STT produced the question (or it errored). We then keep
# updating it INCREMENTALLY -- answer, timings and acted entities fill in as later events arrive --
# instead of waiting for the run to finish. Status is "in_progress" until intent-end.
READY_MARKERS = {"stt-end", "error"}


def _ev_type(ev: Any) -> str:
    return str(getattr(ev, "type", "") or "")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError, TypeError:
        return None


def _seconds(a: str | None, b: str | None) -> float | None:
    """Seconds between two ISO timestamps (rounded), or None if either is missing/unparseable."""
    da, db = _parse_dt(a), _parse_dt(b)
    if da is None or db is None:
        return None
    return round((db - da).total_seconds(), 3)


def _is_tool_error(result: Any) -> bool:
    """Whether a tool result delta's `result` ({data, error}) is a failure.

    The producer's `error` flag is authoritative when set. External (agent-executed)
    tools often leave it unset even on failure, so — mirroring the pi-assistant
    addon's heuristic — fall back to the common error markers in the serialized
    data. Serialized without spaces so the no-space markers match, and the escaped
    variant catches data that is itself a JSON string.
    """
    if not isinstance(result, dict):
        return False
    if result.get("error") is True:
        return True
    data = result.get("data")
    if data is None:
        return False
    serialized = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return (
        "MCP error" in serialized
        or '"isError":true' in serialized
        or '"success":false' in serialized
        or '\\"success\\":false' in serialized
    )


def flatten_run(events: list[Any]) -> dict[str, Any]:
    """Flatten a list of pipeline events into the fields a display needs. Pure / HA-free."""
    first_ts: dict[str, str] = {}
    first_token_ts: str | None = None
    tts_streaming_ts: str | None = None
    error: dict[str, Any] | None = None
    saw_intent_end = False
    tool_calls = 0
    tool_names: list[str] = []
    tool_error_count = 0
    failed_tools: list[str] = []
    out: dict[str, Any] = {}

    for ev in events:
        t = _ev_type(ev)
        ts = getattr(ev, "timestamp", None)
        if t not in first_ts and ts:
            first_ts[t] = ts
        d = getattr(ev, "data", None) or {}

        if t == "run-start":
            out["conversation_id"] = d.get("conversation_id")
            out["satellite_id"] = d.get("satellite_id")
            out["pipeline_id"] = d.get("pipeline")
            out["language"] = d.get("language")
        elif t == "wake_word-end":
            wake = d.get("wake_word_output") or {}
            out["wake_word"] = wake.get("wake_word_phrase") or wake.get("wake_word_id")
        elif t == "stt-start":
            out["stt_engine"] = d.get("engine")
        elif t == "stt-end":
            out["question"] = (d.get("stt_output") or {}).get("text")
        elif t == "intent-start":
            out["engine"] = d.get("engine")
            out["device_id"] = d.get("device_id")
        elif t == "intent-progress":
            # Streaming-input TTS starts playback mid-intent: satellites (wyoming, esphome)
            # begin speaking on this marker, before tts-start. It is the true "assistant
            # starts speaking" moment on fully-streamed setups.
            if tts_streaming_ts is None and d.get("tts_start_streaming"):
                tts_streaming_ts = ts
            delta = d.get("chat_log_delta") or {}
            # Time to first token = the first model output of ANY kind, not just
            # answer text. A reasoning model streams `thinking_content`, and a
            # tool-using turn streams `tool_calls`, both well before the final
            # `content` (the spoken answer). Counting only `content` collapses TTFT
            # onto the response time on every thinking/tool turn.
            if first_token_ts is None and (
                delta.get("content")
                or delta.get("thinking_content")
                or delta.get("tool_calls")
            ):
                first_token_ts = ts
            # The LLM's tool invocations arrive as chat_log_delta.tool_calls (a list per delta).
            for call in delta.get("tool_calls") or []:
                tool_calls += 1
                if isinstance(call, dict) and call.get("tool_name"):
                    tool_names.append(call["tool_name"])
            # A tool's outcome arrives later as a separate `role: tool_result` delta
            # (chat_log fires the listener with ToolResultContent.as_dict()); inspect it
            # so the pipeline view sees tool failures, not just the call count.
            if delta.get("role") == "tool_result" and _is_tool_error(
                delta.get("result")
            ):
                tool_error_count += 1
                if delta.get("tool_name"):
                    failed_tools.append(delta["tool_name"])
        elif t == "intent-end":
            saw_intent_end = True
            out["processed_locally"] = d.get("processed_locally")
            intent_output = d.get("intent_output") or {}
            out["continue_conversation"] = intent_output.get("continue_conversation")
            response = intent_output.get("response") or {}
            speech = ((response.get("speech") or {}).get("plain") or {}).get("speech")
            if speech:
                out["answer"] = speech
            out["response_type"] = response.get("response_type")
        elif t == "tts-start":
            out.setdefault("answer", d.get("tts_input"))
            out["tts_engine"] = d.get("engine")
            out["tts_voice"] = d.get("voice")
        elif t == "error":
            error = d

    finished = "run-end" in first_ts
    # Incremental status: question is known but the agent is still working until intent-end.
    # Deliberately a FAITHFUL mirror of HA's events — a run cancelled mid-flight (run-end
    # without intent-end or error, e.g. an automation stopping the satellite's own voice
    # assistant) stays "in_progress", exactly as HA's own debug UI shows it. No derived
    # status values: if HA core ever emits a proper terminal event for cancellation, it
    # must surface here unchanged, not be masked by our own inference. Consumers that need
    # terminality should read ``finished`` / round_key's e: phase instead.
    if error:
        out["status"] = "error"
    elif saw_intent_end:
        out["status"] = "success"
    else:
        out["status"] = "in_progress"
    out["error_message"] = (error or {}).get("message") if error else None
    out["error_code"] = (error or {}).get("code") if error else None

    # Count a local (non-LLM) intent execution as one "tool call" too: on a local round the matched
    # intent is the equivalent of an LLM tool invocation (LLM rounds already count their tool calls).
    processed_locally = out.get("processed_locally")
    local_intent = (
        1 if (processed_locally is True and saw_intent_end and error is None) else 0
    )
    out["tool_calls"] = tool_calls + local_intent
    out["tool_names"] = tool_names + (["intent"] if local_intent else [])
    out["tool_error_count"] = tool_error_count
    out["failed_tools"] = failed_tools

    out["used_llm"] = processed_locally is False
    out["mode"] = (
        "llm"
        if processed_locally is False
        else ("local" if processed_locally is True else None)
    )

    out["question_time"] = _parse_dt(first_ts.get("run-start"))
    out["wake_word_seconds"] = _seconds(
        first_ts.get("wake_word-start"), first_ts.get("wake_word-end")
    )
    # STT processing time = end of speech (VAD end) -> STT text ready (matches the satellite's metric).
    out["stt_seconds"] = _seconds(
        first_ts.get("stt-vad-end") or first_ts.get("stt-start"),
        first_ts.get("stt-end"),
    )
    out["speech_seconds"] = _seconds(
        first_ts.get("stt-vad-start"), first_ts.get("stt-vad-end")
    )
    out["intent_seconds"] = _seconds(
        first_ts.get("intent-start"), first_ts.get("intent-end")
    )
    # No streamed token (local intent, or any agent that answers in one shot) → fall back to
    # intent-end, the response-ready moment, but only on a successful turn so a failure reports none.
    no_stream_fallback = (
        first_ts.get("intent-end") if (saw_intent_end and error is None) else None
    )
    first_token = first_token_ts if first_token_ts is not None else no_stream_fallback
    out["ttft_seconds"] = _seconds(first_ts.get("intent-start"), first_token)
    out["tts_seconds"] = _seconds(first_ts.get("tts-start"), first_ts.get("tts-end"))
    # User-perceived silence: from when the user stops speaking (VAD end, fallback STT end) to when
    # the assistant starts speaking. With streaming-input TTS that is the tts_start_streaming
    # marker (mid-intent, before tts-start); otherwise tts-start. The single number that matters
    # for voice latency.
    out["latency_seconds"] = _seconds(
        first_ts.get("stt-vad-end") or first_ts.get("stt-end"),
        tts_streaming_ts or first_ts.get("tts-start"),
    )
    out["total_seconds"] = _seconds(first_ts.get("run-start"), first_ts.get("run-end"))
    # Terminal marker: after run-end no further event (answer, tts) can arrive for this run.
    out["finished"] = finished
    return out


def round_key(run: dict[str, Any] | None, count: int | None) -> str | None:
    """Semantic round key "<phase>:<n>[:<answer_hash>]" for one flattened run, or None if nothing
    to show yet (wake-only / mid-STT, or count unknown). ``<n>`` is the cumulative conversation
    number, the round discriminator. Phases:

        "q:<n>"          satellite round, question in, no answer yet -> question phase
        "a:<n>:<hash8>"  satellite round, answer present (hash changes on upgrade) -> answer phase
        "e:<n>"          satellite round, errored, aborted, or finished without any speech
        "x:<n>"          NON-satellite round (HA app etc.)      -> (a display skips these)

    A run that reaches run-end with no answer maps to "e:" — it is terminal (no answer can arrive
    anymore), so a consumer stops waiting instead of idling in "q:" until its own timeout. For the
    same reason an errored run maps to "e:" even without a question (a pre-STT failure like
    stt-no-text-recognized is published and counted, so the consumer must see it end). The
    intent-end -> tts-start window deliberately stays "q:" (only ``finished`` flips the phase), so
    a normal streaming round never flashes an error phase mid-round.
    """
    if not run or count is None:
        return None
    is_error = run.get("status") == "error"
    if not run.get("question") and not is_error:
        return None  # nothing to show yet (wake-only / mid-STT)
    if not run.get("satellite_id"):
        return f"x:{count}"  # non-satellite (HA app, web/text Assist) -> consumer skips
    if is_error:
        return f"e:{count}"
    answer = run.get("answer")
    if answer:
        digest = hashlib.blake2s(str(answer).encode("utf-8"), digest_size=4).hexdigest()
        return f"a:{count}:{digest}"
    if run.get("finished"):
        # Terminal with no speech: silent success OR aborted mid-flight -> e:, not a stuck "q:".
        return f"e:{count}"
    return f"q:{count}"


def _flatten_with_id(run_id: str, run: Any) -> dict[str, Any]:
    result = flatten_run(getattr(run, "events", None) or [])
    result["run_id"] = run_id
    return result


def _newest_ready_run(runs: Any) -> tuple[str, str, Any] | None:
    """(timestamp, run_id, run) of the newest run in one pipeline's run dict that has a question."""
    best: tuple[str, str, Any] | None = None
    for run_id, run in runs.items():
        events = getattr(run, "events", None) or []
        if not ({_ev_type(ev) for ev in events} & READY_MARKERS):
            continue  # skip runs with no question yet (wake-only / mid-STT)
        stamp = getattr(run, "timestamp", "") or ""
        if best is None or stamp > best[0]:
            best = (stamp, run_id, run)
    return best


def parse_all(hass: Any) -> dict[str, Any]:
    """Scan the debug store and return the global latest run plus the latest run per pipeline (assist).

    The debug store is keyed by pipeline id, so each key is one assist. Returns
    ``{"latest": <flat | None>, "by_pipeline": {pipeline_id: <flat>}}``.
    """
    out: dict[str, Any] = {"latest": None, "by_pipeline": {}}
    data = hass.data.get(ASSIST_PIPELINE_KEY)
    if data is None:
        return out  # assist_pipeline not loaded (yet)
    debug = getattr(data, "pipeline_debug", None)
    if debug is None:
        global _warned_unrecognized_store
        if not _warned_unrecognized_store:
            _warned_unrecognized_store = True
            _LOGGER.warning(
                "assist_pipeline data has no pipeline_debug store; the internal"
                " layout this integration reads may have changed in this Home"
                " Assistant version - sensors will stay empty"
            )
        return out
    if not debug:
        return out

    best: tuple[str, str, Any] | None = None  # global newest
    for pipeline_id, runs in debug.items():
        newest = _newest_ready_run(runs)
        if newest is None:
            continue
        out["by_pipeline"][pipeline_id] = _flatten_with_id(newest[1], newest[2])
        if best is None or newest[0] > best[0]:
            best = newest

    if best is not None:
        out["latest"] = _flatten_with_id(best[1], best[2])
    return out
