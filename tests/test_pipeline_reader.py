"""Unit tests for the pure event-flattening logic (no Home Assistant required).

Loads pipeline_reader.py by file path so the package __init__ (which imports Home Assistant) is not
executed.
"""

from __future__ import annotations

import importlib.util
import pathlib
import types
from datetime import datetime

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "assist_monitor"
    / "pipeline_reader.py"
)
_spec = importlib.util.spec_from_file_location(
    "assist_monitor_pipeline_reader", _MODULE_PATH
)
assert _spec and _spec.loader
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


def _ev(event_type: str, data, timestamp: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(type=event_type, data=data, timestamp=timestamp)


def _sample_events(*, with_answer: bool = True) -> list[types.SimpleNamespace]:
    events = [
        _ev(
            "run-start",
            {
                "conversation_id": "C1",
                "satellite_id": "assist_satellite.x",
                "language": "zh",
            },
            "2026-06-29T11:58:57.184685+00:00",
        ),
        _ev(
            "wake_word-end",
            {
                "wake_word_output": {
                    "wake_word_phrase": "Okay Nabu",
                    "wake_word_id": "ww",
                }
            },
            "2026-06-29T11:58:57.5+00:00",
        ),
        _ev("stt-start", {"engine": "stt.sensevoice"}, "2026-06-29T11:58:58.0+00:00"),
        _ev("stt-vad-start", {"timestamp": 1100}, "2026-06-29T11:58:58.281840+00:00"),
        _ev("stt-vad-end", {"timestamp": 2750}, "2026-06-29T11:58:59.932073+00:00"),
        _ev(
            "stt-end",
            {"stt_output": {"text": "還看前面呢"}},
            "2026-06-29T11:59:00.156452+00:00",
        ),
        _ev(
            "intent-start",
            {
                "engine": "conversation.voice_assistant",
                "satellite_id": "assist_satellite.x",
                "device_id": "dev123",
            },
            "2026-06-29T11:59:00.156576+00:00",
        ),
    ]
    if with_answer:
        events += [
            _ev(
                "intent-progress",
                {"chat_log_delta": {"role": "assistant"}},
                "2026-06-29T11:59:03.206865+00:00",
            ),
            _ev(
                "intent-progress",
                {
                    "chat_log_delta": {
                        "tool_calls": [
                            {
                                "tool_name": "web_search",
                                "tool_args": {"query": "x"},
                                "id": "call_1",
                                "external": True,
                            }
                        ]
                    }
                },
                "2026-06-29T11:59:03.206900+00:00",
            ),
            _ev(
                "intent-progress",
                {"chat_log_delta": {"content": "前"}},
                "2026-06-29T11:59:03.206928+00:00",
            ),
            _ev(
                "intent-end",
                {
                    "processed_locally": False,
                    "intent_output": {
                        "continue_conversation": False,
                        "response": {
                            "speech": {
                                "plain": {
                                    "speech": "前面先幫你打開入口燈跟浴室燈，後來又把它們關掉了。"
                                }
                            },
                            "response_type": "action_done",
                            "data": {
                                "success": [
                                    {
                                        "name": "浴室燈",
                                        "type": "entity",
                                        "id": "light.yu_shi_deng",
                                    }
                                ],
                                "failed": [],
                            },
                        },
                    },
                },
                "2026-06-29T11:59:03.369690+00:00",
            ),
            _ev(
                "tts-start",
                {
                    "tts_input": "前面先幫你打開入口燈跟浴室燈，後來又把它們關掉了。",
                    "engine": "tts.edge_tts",
                    "voice": "zh-TW-HsiaoChenNeural",
                },
                "2026-06-29T11:59:03.369853+00:00",
            ),
            _ev("tts-end", {"tts_output": {}}, "2026-06-29T11:59:03.372314+00:00"),
            _ev("run-end", None, "2026-06-29T11:59:03.374949+00:00"),
        ]
    return events


def test_complete_run() -> None:
    r = pr.flatten_run(_sample_events())
    assert r["question"] == "還看前面呢"
    assert r["answer"].startswith("前面先幫你打開入口燈")
    assert r["mode"] == "llm"
    assert r["used_llm"] is True
    assert r["status"] == "success"
    assert r["tool_calls"] == 1
    assert r["tool_error_count"] == 0
    assert r["failed_tools"] == []
    # vad-end (11:58:59.932) -> tts-start (11:59:03.369) = user-perceived silence.
    assert r["latency_seconds"] is not None and r["latency_seconds"] > 0
    assert r["tool_names"] == ["web_search"]
    assert r["wake_word"] == "Okay Nabu"
    assert r["language"] == "zh"
    assert r["stt_engine"] == "stt.sensevoice"
    assert r["tts_engine"] == "tts.edge_tts"
    assert r["tts_voice"] == "zh-TW-HsiaoChenNeural"
    assert r["device_id"] == "dev123"
    assert r["continue_conversation"] is False
    assert r["response_type"] == "action_done"
    assert r["satellite_id"] == "assist_satellite.x"
    assert round(r["intent_seconds"], 1) == 3.2
    assert r["ttft_seconds"] is not None and r["ttft_seconds"] > 0
    assert isinstance(r["question_time"], datetime)
    assert r["total_seconds"] is not None


def test_ttft_counts_thinking_before_answer_text() -> None:
    """TTFT marks the first model output of ANY kind.

    A reasoning model streams `thinking_content` (and a tool turn streams
    `tool_calls`) well before the spoken-answer `content`. TTFT must lock onto
    that first token, not the late answer text — otherwise it collapses onto the
    response time on every thinking/tool turn.
    """
    events = [
        _ev(
            "intent-start",
            {"engine": "conversation.voice_assistant"},
            "2026-06-29T12:00:00.000000+00:00",
        ),
        # First real output: a reasoning delta at +1.0s.
        _ev(
            "intent-progress",
            {"chat_log_delta": {"thinking_content": "讓我想想"}},
            "2026-06-29T12:00:01.000000+00:00",
        ),
        _ev(
            "intent-progress",
            {
                "chat_log_delta": {
                    "tool_calls": [
                        {
                            "tool_name": "ha_get_history",
                            "id": "call_1",
                            "external": True,
                        }
                    ]
                }
            },
            "2026-06-29T12:00:02.000000+00:00",
        ),
        # The spoken answer only starts at +9.0s, after thinking + tools.
        _ev(
            "intent-progress",
            {"chat_log_delta": {"content": "你剛剛出門"}},
            "2026-06-29T12:00:09.000000+00:00",
        ),
        _ev(
            "intent-end",
            {"processed_locally": False, "intent_output": {"response": {}}},
            "2026-06-29T12:00:09.200000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    # 1.0s (first thinking delta), NOT 9.0s (the answer text).
    assert r["ttft_seconds"] == 1.0


def test_tool_error_detected_from_tool_result_delta() -> None:
    """A failing tool surfaces via its `role: tool_result` delta, not the call delta."""
    events = [
        _ev(
            "intent-start",
            {"engine": "conversation.voice_assistant"},
            "2026-06-29T12:00:00.000000+00:00",
        ),
        _ev(
            "intent-progress",
            {
                "chat_log_delta": {
                    "tool_calls": [
                        {"tool_name": "ha_get_state", "id": "c1", "external": True}
                    ]
                }
            },
            "2026-06-29T12:00:01.000000+00:00",
        ),
        _ev(
            "intent-progress",
            {
                "chat_log_delta": {
                    "role": "tool_result",
                    "tool_call_id": "c1",
                    "tool_name": "ha_get_state",
                    "result": {
                        "data": {"success": False, "error": "entity not found"},
                        "error": False,
                    },
                }
            },
            "2026-06-29T12:00:02.000000+00:00",
        ),
        _ev(
            "intent-end",
            {"processed_locally": False, "intent_output": {"response": {}}},
            "2026-06-29T12:00:02.500000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["tool_calls"] == 1
    assert r["tool_error_count"] == 1
    assert r["failed_tools"] == ["ha_get_state"]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        # The producer's flag is authoritative, even on a benign-looking payload.
        ({"data": {"error": "HomeAssistantError"}, "error": True}, True),
        # Unflagged (external agent): fall back to the payload markers.
        ({"data": {"result": "MCP error -32602: bad args"}, "error": False}, True),
        ({"data": {"success": True}, "error": False}, False),
        (None, False),
    ],
)
def test_tool_error_flag_then_payload_fallback(result, expected: bool) -> None:
    """`result.error` wins; only an unflagged result is scanned for error markers."""
    delta = {"role": "tool_result", "tool_name": "ha_get_state", "result": result}
    events = [
        _ev("intent-progress", {"chat_log_delta": delta}, "2026-06-29T12:00:02+00:00"),
    ]
    r = pr.flatten_run(events)
    assert r["tool_error_count"] == int(expected)
    assert r["failed_tools"] == (["ha_get_state"] if expected else [])


def test_local_intent_ttft_falls_back_to_intent_end() -> None:
    """Local intents stream no tokens; TTFT uses intent-end so it isn't blank."""
    events = [
        _ev(
            "intent-start",
            {"engine": "conversation.home_assistant"},
            "2026-06-29T12:00:00.000000+00:00",
        ),
        _ev(
            "intent-end",
            {
                "processed_locally": True,
                "intent_output": {
                    "response": {"speech": {"plain": {"speech": "好的"}}}
                },
            },
            "2026-06-29T12:00:00.080000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["mode"] == "local"
    assert r["ttft_seconds"] == 0.08
    assert r["intent_seconds"] == 0.08


def test_non_streaming_llm_ttft_falls_back_to_intent_end() -> None:
    """A non-local agent that answers in one shot (no streamed deltas) still reports TTFT."""
    events = [
        _ev(
            "intent-start",
            {"engine": "conversation.some_agent"},
            "2026-06-29T12:00:00.000000+00:00",
        ),
        _ev(
            "intent-end",
            {
                "processed_locally": False,
                "intent_output": {
                    "response": {"speech": {"plain": {"speech": "done"}}}
                },
            },
            "2026-06-29T12:00:00.500000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["used_llm"] is True
    assert r["ttft_seconds"] == 0.5


def test_failed_local_intent_reports_no_ttft() -> None:
    """An errored turn must not report a TTFT (no answer/token was produced)."""
    events = [
        _ev(
            "intent-start",
            {"engine": "conversation.home_assistant"},
            "2026-06-29T12:00:00.000000+00:00",
        ),
        _ev(
            "error",
            {"code": "no_intent_match", "message": "Sorry"},
            "2026-06-29T12:00:00.050000+00:00",
        ),
        _ev(
            "intent-end",
            {"processed_locally": True, "intent_output": {"response": {}}},
            "2026-06-29T12:00:00.080000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["status"] == "error"
    assert r["ttft_seconds"] is None


def test_wake_word_seconds() -> None:
    """Wake-word detection latency = wake_word-start -> wake_word-end."""
    events = [
        _ev("wake_word-start", {}, "2026-06-29T12:00:00.000000+00:00"),
        _ev(
            "wake_word-end",
            {"wake_word_output": {"wake_word_phrase": "Hey"}},
            "2026-06-29T12:00:00.300000+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["wake_word_seconds"] == 0.3


def test_in_progress_partial() -> None:
    """Before intent-end: the question is known, status is in_progress, no answer yet."""
    r = pr.flatten_run(_sample_events(with_answer=False))
    assert r["question"] == "還看前面呢"
    assert r.get("answer") is None
    assert r["status"] == "in_progress"
    assert r["used_llm"] is False
    assert r["stt_seconds"] is not None  # vad-end -> stt-end already measurable


def test_empty() -> None:
    r = pr.flatten_run([])
    assert r["status"] == "in_progress"
    assert r.get("question") is None


def _fake_run(events, timestamp):
    return types.SimpleNamespace(events=events, timestamp=timestamp)


def test_parse_all_latest_and_per_pipeline() -> None:
    # Two pipelines (the debug store is keyed by pipeline id); parse_all picks the global latest and
    # the newest run within each pipeline.
    def evs(pid, text, ts):
        return [
            _ev("run-start", {"conversation_id": "c", "pipeline": pid}, ts),
            _ev("stt-end", {"stt_output": {"text": text}}, ts),
            _ev(
                "intent-end",
                {"processed_locally": True, "intent_output": {"response": {}}},
                ts,
            ),
        ]

    debug = {
        "pipe_a": {
            "old": _fake_run(
                evs("pipe_a", "舊A", "2026-06-29T10:00:00+00:00"),
                "2026-06-29T10:00:00+00:00",
            ),
            "new": _fake_run(
                evs("pipe_a", "新A", "2026-06-29T12:00:00+00:00"),
                "2026-06-29T12:00:00+00:00",
            ),
        },
        "pipe_b": {
            "only": _fake_run(
                evs("pipe_b", "B", "2026-06-29T11:00:00+00:00"),
                "2026-06-29T11:00:00+00:00",
            ),
        },
    }
    hass = types.SimpleNamespace(
        data={"assist_pipeline": types.SimpleNamespace(pipeline_debug=debug)}
    )

    result = pr.parse_all(hass)
    assert result["latest"]["question"] == "新A"  # globally newest
    assert set(result["by_pipeline"]) == {"pipe_a", "pipe_b"}
    assert result["by_pipeline"]["pipe_a"]["question"] == "新A"  # newest within pipe_a
    assert result["by_pipeline"]["pipe_b"]["question"] == "B"
    assert result["by_pipeline"]["pipe_a"]["pipeline_id"] == "pipe_a"
    # local (processed_locally) round with an intent-end -> the intent counts as one tool call
    assert result["by_pipeline"]["pipe_b"]["tool_calls"] == 1
    assert result["by_pipeline"]["pipe_b"]["mode"] == "local"


def test_parse_all_empty_store() -> None:
    hass = types.SimpleNamespace(data={})
    result = pr.parse_all(hass)
    assert result == {"latest": None, "by_pipeline": {}}


def test_parse_all_warns_once_on_unrecognized_store(caplog) -> None:
    """Store present but without pipeline_debug: degrade to empty and warn exactly once."""
    pr._warned_unrecognized_store = False
    hass = types.SimpleNamespace(data={"assist_pipeline": types.SimpleNamespace()})

    assert pr.parse_all(hass) == {"latest": None, "by_pipeline": {}}
    assert "pipeline_debug" in caplog.text

    caplog.clear()
    assert pr.parse_all(hass) == {"latest": None, "by_pipeline": {}}
    assert caplog.text == ""  # warned only on the first sighting


def test_finished_flag() -> None:
    assert pr.flatten_run(_sample_events())["finished"] is True
    assert pr.flatten_run(_sample_events(with_answer=False))["finished"] is False


def test_round_key_phases() -> None:
    done = pr.flatten_run(_sample_events())
    key = pr.round_key(done, 42)
    assert key is not None and key.startswith("a:42:") and len(key) == len("a:42:") + 8

    in_progress = pr.flatten_run(_sample_events(with_answer=False))
    assert pr.round_key(in_progress, 42) == "q:42"

    assert pr.round_key(done, None) is None
    assert pr.round_key(None, 42) is None

    assert pr.round_key(dict(done, satellite_id=None), 42) == "x:42"
    assert pr.round_key(dict(done, status="error", answer=None), 42) == "e:42"


def test_round_key_finished_without_answer_is_terminal() -> None:
    """A run that reaches run-end with no speech maps to e:, not a forever-stuck q:."""
    events = _sample_events(with_answer=False) + [
        _ev(
            "intent-end",
            {
                "processed_locally": False,
                "intent_output": {
                    "response": {
                        "speech": {"plain": {"speech": None}},
                        "response_type": "query_answer",
                        "data": {"success": [], "failed": []},
                    }
                },
            },
            "2026-07-04T10:38:55.0+00:00",
        ),
        _ev("run-end", None, "2026-07-04T10:38:55.1+00:00"),
    ]
    r = pr.flatten_run(events)
    assert r["status"] == "success"
    assert r.get("answer") is None
    assert r["finished"] is True
    assert pr.round_key(r, 273) == "e:273"


def test_round_key_stays_q_between_intent_end_and_run_end() -> None:
    """intent-end seen but run not finished: empty answer must NOT flash e: mid-round."""
    events = _sample_events(with_answer=False) + [
        _ev(
            "intent-end",
            {
                "processed_locally": False,
                "intent_output": {
                    "response": {
                        "speech": {"plain": {"speech": None}},
                        "response_type": "query_answer",
                        "data": {"success": [], "failed": []},
                    }
                },
            },
            "2026-07-04T10:38:55.0+00:00",
        ),
    ]
    r = pr.flatten_run(events)
    assert r["status"] == "success" and r["finished"] is False
    assert pr.round_key(r, 273) == "q:273"


def test_cancelled_run_mirrors_ha_but_round_key_is_terminal() -> None:
    """run-end with NO intent-end and NO error = the run was cancelled mid-flight.

    Production case: a sentence-trigger automation ("關閉音樂") stopped the satellite's own
    voice assistant, killing the pipeline ~20ms after intent-start. Status stays a faithful
    mirror of HA (in_progress — HA emits no terminal event for cancellation), but round_key
    (our own consumer contract) must flip to the terminal e: phase, not stick at q:.
    """
    events = _sample_events(with_answer=False) + [
        _ev("run-end", None, "2026-07-13T00:23:43.9+00:00"),
    ]
    r = pr.flatten_run(events)
    assert r["status"] == "in_progress"  # mirrors HA's own debug UI, no derived status
    assert r["finished"] is True
    assert r.get("answer") is None
    assert pr.round_key(r, 521) == "e:521"


def test_latency_uses_early_tts_streaming_anchor() -> None:
    """With streaming-input TTS the satellite starts speaking at the tts_start_streaming
    intent-progress marker (before tts-start); latency must end there, not at tts-start."""
    events = _sample_events()
    # Without the marker, latency ends at tts-start: vad-end (11:58:59.932073) -> 11:59:03.369853.
    assert pr.flatten_run(events)["latency_seconds"] == 3.438
    events.insert(
        10,  # after the streamed deltas, before intent-end
        _ev(
            "intent-progress",
            {"tts_start_streaming": True},
            "2026-06-29T11:59:03.250000+00:00",
        ),
    )
    r = pr.flatten_run(events)
    # vad-end -> tts_start_streaming marker (11:59:03.25), not tts-start.
    assert r["latency_seconds"] == 3.318
    # The marker carries no chat_log_delta: TTFT and tool counting must be unaffected.
    assert r["ttft_seconds"] == pr.flatten_run(_sample_events())["ttft_seconds"]
    assert r["tool_calls"] == 1


def test_round_key_error_before_question_is_terminal() -> None:
    """A pre-STT failure (e.g. stt-no-text-recognized) has no question but IS published and
    counted; the consumer must see it end as e:, not idle in the previous round."""
    events = [
        _ev(
            "run-start",
            {"conversation_id": "c", "satellite_id": "assist_satellite.x"},
            "2026-07-13T09:00:00.0+00:00",
        ),
        _ev(
            "error",
            {"code": "stt-no-text-recognized", "message": "no text"},
            "2026-07-13T09:00:02.0+00:00",
        ),
        _ev("run-end", None, "2026-07-13T09:00:02.1+00:00"),
    ]
    r = pr.flatten_run(events)
    assert r["status"] == "error"
    assert r.get("question") is None
    assert pr.round_key(r, 99) == "e:99"
    # Non-satellite variant stays a consumer-skipped x: round.
    assert pr.round_key(dict(r, satellite_id=None), 99) == "x:99"
    # No question and no error is still "nothing to show yet".
    assert pr.round_key({"satellite_id": "assist_satellite.x"}, 99) is None
