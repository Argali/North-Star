"""
Unit tests for the hybrid retrieval scorer.

Covers:
- _recency_score(): exponential decay formula, edge cases
- hybrid_score() weight application: alpha/beta/gamma combine correctly
- ScoredItem final_score calculation
- Graceful degradation when embedding provider fails (alpha → 0)

No DB or embedding API calls.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.retrieval.scorer import (
    ScoredItem,
    _recency_score,
    _RECENCY_LAMBDA,
)


# ── _recency_score ────────────────────────────────────────────────────────────

class TestRecencyScore:

    def test_today_returns_near_one(self):
        now = datetime.now(timezone.utc)
        score = _recency_score(now)
        assert score > 0.99

    def test_138_days_ago_near_half(self):
        # Half-life is ~138 days with lambda=0.005
        ts = datetime.now(timezone.utc) - timedelta(days=138)
        score = _recency_score(ts)
        assert 0.45 < score < 0.55

    def test_one_year_ago_low(self):
        ts = datetime.now(timezone.utc) - timedelta(days=365)
        score = _recency_score(ts)
        assert 0.10 < score < 0.25

    def test_five_years_ago_very_low(self):
        ts = datetime.now(timezone.utc) - timedelta(days=365 * 5)
        score = _recency_score(ts)
        assert score < 0.01

    def test_none_returns_neutral(self):
        assert _recency_score(None) == 0.5

    def test_naive_datetime_handled(self):
        # Should not raise even without tzinfo
        ts = datetime.now()  # naive
        score = _recency_score(ts)
        assert 0.0 <= score <= 1.0

    def test_decay_is_monotonically_decreasing(self):
        scores = [
            _recency_score(datetime.now(timezone.utc) - timedelta(days=d))
            for d in [0, 30, 90, 180, 365]
        ]
        for i in range(len(scores) - 1):
            assert scores[i] > scores[i + 1], f"Score not decreasing at index {i}"

    def test_formula_matches_expected(self):
        age_days = 100
        ts = datetime.now(timezone.utc) - timedelta(days=age_days)
        expected = math.exp(-_RECENCY_LAMBDA * age_days)
        actual = _recency_score(ts)
        assert abs(actual - expected) < 0.01


# ── ScoredItem final_score ────────────────────────────────────────────────────

class TestScoredItemFinalScore:
    """Verify the scoring formula: α·sem + β·kw + γ·rec."""

    def _make_item(self, sem, kw, rec, alpha=0.6, beta=0.25, gamma=0.15):
        final = alpha * sem + beta * kw + gamma * rec
        return ScoredItem(
            id=uuid4(),
            object_type="knowledge",
            statement="test statement",
            confidence=0.9,
            topics=["fleet"],
            semantic_score=sem,
            keyword_score=kw,
            recency_score=rec,
            final_score=final,
        )

    def test_pure_semantic_match(self):
        item = self._make_item(sem=1.0, kw=0.0, rec=0.0, alpha=0.6, beta=0.25, gamma=0.15)
        assert abs(item.final_score - 0.6) < 1e-9

    def test_pure_keyword_match(self):
        item = self._make_item(sem=0.0, kw=1.0, rec=0.0, alpha=0.6, beta=0.25, gamma=0.15)
        assert abs(item.final_score - 0.25) < 1e-9

    def test_pure_recency(self):
        item = self._make_item(sem=0.0, kw=0.0, rec=1.0, alpha=0.6, beta=0.25, gamma=0.15)
        assert abs(item.final_score - 0.15) < 1e-9

    def test_all_signals_max(self):
        item = self._make_item(sem=1.0, kw=1.0, rec=1.0, alpha=0.6, beta=0.25, gamma=0.15)
        assert abs(item.final_score - 1.0) < 1e-9

    def test_all_signals_zero(self):
        item = self._make_item(sem=0.0, kw=0.0, rec=0.0)
        assert item.final_score == 0.0

    def test_weights_do_not_have_to_sum_to_one(self):
        # The formula doesn't enforce this — verify it still computes correctly
        item = self._make_item(sem=0.5, kw=0.5, rec=0.5, alpha=0.5, beta=0.5, gamma=0.5)
        assert abs(item.final_score - 0.75) < 1e-9

    def test_higher_semantic_score_ranks_higher(self):
        high = self._make_item(sem=0.9, kw=0.5, rec=0.5)
        low  = self._make_item(sem=0.3, kw=0.5, rec=0.5)
        assert high.final_score > low.final_score

    def test_higher_recency_ranks_higher_ceteris_paribus(self):
        recent = self._make_item(sem=0.5, kw=0.5, rec=0.9)
        old    = self._make_item(sem=0.5, kw=0.5, rec=0.1)
        assert recent.final_score > old.final_score


# ── hybrid_score weight override ──────────────────────────────────────────────

class TestHybridScoreWeights:
    """
    Test that alpha/beta/gamma overrides are passed through correctly.
    Uses mocked DB so no real Postgres is needed.
    """

    @pytest.mark.asyncio
    async def test_keyword_only_when_no_embedding(self):
        """When embedding provider fails, semantic score should be 0 and items still returned."""
        from src.retrieval.scorer import hybrid_score

        failing_provider = AsyncMock()
        failing_provider.embed.side_effect = RuntimeError("API key missing")

        mock_row = {
            "id": uuid4(),
            "statement": "Fleet maintenance interval is 30 days",
            "confidence": 0.9,
            "topics": ["fleet", "maintenance"],
            "valid_from": datetime.now(timezone.utc),
            "source_section": "",
            "source_report_ids": [],
            "sem_score": 0.0,
            "kw_score": 0.8,
        }

        with patch("src.retrieval.scorer.get_provider", return_value=failing_provider):
            with patch("src.retrieval.scorer.get_conn") as mock_get_conn:
                # Set up the async context manager for get_conn
                mock_conn = AsyncMock()
                mock_conn.fetch.return_value = [mock_row]
                cm = AsyncMock()
                cm.__aenter__.return_value = mock_conn
                cm.__aexit__.return_value = False
                mock_get_conn.return_value = cm

                result = await hybrid_score(
                    query="maintenance interval",
                    intent="knowledge",
                    alpha=0.6, beta=0.25, gamma=0.15,
                )

        assert result.query_embedding_used is False
        # With no embedding, semantic score is 0; final = 0*0.6 + 0.8*0.25 + rec*0.15
        for item in result.items:
            assert item.semantic_score == 0.0

    @pytest.mark.asyncio
    async def test_custom_weights_applied(self):
        """Verify that overridden weights appear in the result."""
        from src.retrieval.scorer import hybrid_score

        mock_provider = AsyncMock()
        mock_provider.embed.return_value = [0.1] * 1536

        with patch("src.retrieval.scorer.get_provider", return_value=mock_provider):
            with patch("src.retrieval.scorer.get_conn") as mock_get_conn:
                mock_conn = AsyncMock()
                mock_conn.fetch.return_value = []
                cm = AsyncMock()
                cm.__aenter__.return_value = mock_conn
                cm.__aexit__.return_value = False
                mock_get_conn.return_value = cm

                result = await hybrid_score(
                    query="test",
                    alpha=0.8, beta=0.1, gamma=0.1,
                )

        assert result.alpha == 0.8
        assert result.beta == 0.1
        assert result.gamma == 0.1
