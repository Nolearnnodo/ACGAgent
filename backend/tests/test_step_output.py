import json
from datetime import datetime

from app.agents.executor import _serialize_arguments, _truncate_output
from app.agents.step_output import serialize_json_preview, serialize_step_output
from app.observability.trace_repository import _MAX_JSON_CHARS, _json_dumps


def test_serialize_step_output_truncates_inside_structure_and_keeps_json_valid():
    payload = {
        "rows": [{"index": index, "text": "x" * 500} for index in range(50)],
        "note": "y" * 2000,
    }

    dumped = serialize_step_output(payload, max_chars=1500)
    parsed = json.loads(dumped)

    assert len(dumped) <= 1500
    assert parsed["_step_output_preview"]["truncated"] is True
    assert len(parsed["rows"]) < len(payload["rows"])


def test_executor_output_preview_uses_valid_json_truncation():
    dumped = _truncate_output(
        {"rows": [{"index": index, "text": "x" * 500} for index in range(50)]},
        max_chars=1500,
    )

    parsed = json.loads(dumped)

    assert len(dumped) <= 1500
    assert parsed["_step_output_preview"]["truncated"] is True


def test_executor_argument_preview_uses_valid_json_truncation():
    dumped = _serialize_arguments(
        {"user_prompt": "x" * 5000, "recent_messages": ["y" * 500 for _ in range(20)]},
        max_chars=1500,
    )

    parsed = json.loads(dumped)

    assert len(dumped) <= 1500
    assert parsed["_step_output_preview"]["truncated"] is True


def test_serialize_json_preview_supports_non_json_values_with_default():
    dumped = serialize_json_preview(
        {
            "created_at": datetime(2026, 5, 31, 12, 0, 0),
            "rows": ["x" * 500 for _ in range(50)],
        },
        max_chars=1500,
        default=str,
    )

    parsed = json.loads(dumped)

    assert len(dumped) <= 1500
    assert parsed["_step_output_preview"]["truncated"] is True
    assert "created_at" in parsed


def test_trace_json_dumps_truncates_to_valid_json_preview():
    payload = {
        "messages": [
            {"role": "user", "content": "x" * 1000}
            for _index in range(100)
        ]
    }

    dumped = _json_dumps(payload)
    parsed = json.loads(dumped)

    assert len(dumped) <= _MAX_JSON_CHARS
    assert parsed["_step_output_preview"]["truncated"] is True
    assert len(parsed["messages"]) < len(payload["messages"])
