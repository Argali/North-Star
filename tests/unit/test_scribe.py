"""
Unit tests for the Scribe pipeline.

Covers:
- _normalize(): all three source_type values + fallback
- _classify_contradiction(): temporal / direct / contextual heuristic
- _extract_tool_result(): happy path and missing tool name

No DB or LLM calls — all external dependencies are mocked.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents.scribe.pipeline import (
    ScribePipelineError,
    _classify_contradiction,
    _extract_tool_result,
    _normalize,
)


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:

    def test_conversation_joins_role_and_content(self):
        payload = {
            "messages": [
                {"role": "user",      "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        result = _normalize("conversation", payload)
        assert "[USER] Hello" in result
        assert "[ASSISTANT] Hi there" in result

    def test_conversation_empty_messages(self):
        result = _normalize("conversation", {"messages": []})
        assert result == ""

    def test_task_prefers_log_key(self):
        payload = {"log": "task ran OK", "text": "ignored"}
        assert _normalize("task", payload) == "task ran OK"

    def test_task_falls_back_to_text(self):
        payload = {"text": "fallback text"}
        assert _normalize("task", payload) == "fallback text"

    def test_task_falls_back_to_json(self):
        payload = {"key": "value"}
        result = _normalize("task", payload)
        assert "key" in result
        assert "value" in result

    def test_document_prefers_text_key(self):
        payload = {"text": "document content", "content": "ignored"}
        assert _normalize("document", payload) == "document content"

    def test_document_falls_back_to_content(self):
        payload = {"content": "document content"}
        assert _normalize("document", payload) == "document content"

    def test_document_falls_back_to_json(self):
        payload = {"title": "My Doc"}
        result = _normalize("document", payload)
        data = json.loads(result)
        assert data["title"] == "My Doc"

    def test_unknown_source_type_stringifies_payload(self):
        payload = {"foo": "bar"}
        result = _normalize("unknown_type", payload)
        assert "foo" in result
        assert "bar" in result


# ── _classify_contradiction ───────────────────────────────────────────────────

class TestClassifyContradiction:

    def test_temporal_marker_in_new_statement(self):
        new = "In 2025 the fleet had 150 vehicles"
        old = "The fleet has 120 vehicles"
        assert _classify_contradiction(new, old) == "temporal"

    def test_temporal_marker_in_existing_statement(self):
        new = "The fleet has 120 vehicles"
        old = "Last year the fleet had 100 vehicles"
        assert _classify_contradiction(new, old) == "temporal"

    def test_temporal_quarter_marker(self):
        new = "Q3 maintenance costs were EUR 45,000"
        old = "Maintenance costs are EUR 30,000"
        assert _classify_contradiction(new, old) == "temporal"

    def test_temporal_month_marker(self):
        new = "In March the fuel cost per km was 0.18"
        old = "Fuel cost per km is 0.15"
        assert _classify_contradiction(new, old) == "temporal"

    def test_direct_high_lexical_overlap(self):
        # Same subject, clearly opposing values, no temporal markers
        new = "The standard inspection interval is 30 days"
        old = "The standard inspection interval is 60 days"
        assert _classify_contradiction(new, old) == "direct"

    def test_contextual_different_domains(self):
        new = "Supplier A charges EUR 500 per service"
        old = "Fuel consumption for vehicle 42 is 8.5L/100km"
        assert _classify_contradiction(new, old) == "contextual"

    def test_direct_threshold_boundary(self):
        # Exact same words except one value → very high overlap → direct
        new = "Maximum load capacity is 2500 kg"
        old = "Maximum load capacity is 3000 kg"
        result = _classify_contradiction(new, old)
        assert result in ("direct", "temporal")  # could go either way; must not be contextual

    def test_temporal_current_keyword(self):
        new = "Current fuel price is EUR 1.85 per litre"
        old = "Fuel price is EUR 1.65 per litre"
        assert _classify_contradiction(new, old) == "temporal"


# ── _extract_tool_result ──────────────────────────────────────────────────────

class TestExtractToolResult:

    def _make_response(self, blocks):
        resp = MagicMock()
        resp.content = blocks
        resp.stop_reason = "tool_use"
        return resp

    def _make_tool_block(self, name, input_data):
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.input = input_data
        return block

    def test_extracts_matching_tool(self):
        block = self._make_tool_block("generate_report", {"title": "Test"})
        response = self._make_response([block])
        result = _extract_tool_result(response, "generate_report")
        assert result == {"title": "Test"}

    def test_raises_when_tool_not_present(self):
        block = self._make_tool_block("other_tool", {"data": 1})
        response = self._make_response([block])
        with pytest.raises(ValueError, match="generate_report"):
            _extract_tool_result(response, "generate_report")

    def test_raises_on_empty_content(self):
        response = self._make_response([])
        with pytest.raises(ValueError):
            _extract_tool_result(response, "generate_report")

    def test_picks_correct_tool_when_multiple_blocks(self):
        block1 = self._make_tool_block("wrong_tool", {"x": 1})
        block2 = self._make_tool_block("extract_candidates", {"knowledge_candidates": []})
        response = self._make_response([block1, block2])
        result = _extract_tool_result(response, "extract_candidates")
        assert result == {"knowledge_candidates": []}
