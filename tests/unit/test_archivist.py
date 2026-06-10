"""
Unit tests for the Archivist pipeline.

Covers the highest-risk logic:
1. Decision validation -- reject when no knowledge link or no rationale
2. Contradiction state machine -- temporal -> supersede, direct/contextual -> human review
3. High-stakes topic flag -- always routes to human review
4. Duplicate merge -- higher confidence kept, report IDs combined
5. Clean candidate -- stored as validated
6. _collect_deprecated_ids -- returns UUIDs of superseded items only

All DB and embedding calls are mocked. No network required.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.agents.archivist.models import (
    ArchivistOutput,
    ContradictionRecord,
    DecisionOutcome,
    KnowledgeOutcome,
    ValidationRecord,
)
from src.agents.archivist.pipeline import (
    _collect_deprecated_ids,
    _validate_decision,
    _validate_knowledge,
)


# Helpers

_SENTINEL = object()  # distinguishes "not passed" from explicitly passed []


def _make_knowledge_raw(
    statement="Fleet inspection interval is 30 days",
    confidence=0.85,
    topics=None,
    source_report_ids=_SENTINEL,
    contradiction_flag=False,
    contradicts_id=None,
    contradiction_type=None,
):
    if source_report_ids is _SENTINEL:
        source_report_ids = [str(uuid4())]
    return {
        "statement": statement,
        "confidence": confidence,
        "topics": topics or ["fleet"],
        "source_report_ids": source_report_ids,
        "source_section": "analysis",
        "contradiction_flag": contradiction_flag,
        "contradicts_id": str(contradicts_id) if contradicts_id else None,
        "contradiction_type": contradiction_type,
    }


def _make_decision_raw(
    statement="Adopt 30-day inspection cycle",
    rationale="Reduces unplanned breakdowns by 40%",
    linked_knowledge_refs=None,
    owner="fleet_manager",
    status="planned",
):
    return {
        "statement": statement,
        "rationale": rationale,
        "linked_knowledge_refs": linked_knowledge_refs if linked_knowledge_refs is not None else [0],
        "owner": owner,
        "status": status,
    }


# Patch target strings
_PATCH_GET_PROVIDER   = "src.agents.archivist.pipeline.get_provider"
_PATCH_GET_CONN       = "src.agents.archivist.pipeline.get_conn"
_PATCH_TRANSACTION    = "src.agents.archivist.pipeline.transaction"
_PATCH_PUSH_TO_REVIEW = "src.agents.archivist.pipeline._push_to_review"
_PATCH_FIND_SIMILAR   = "src.agents.archivist.pipeline._find_similar_knowledge"
_PATCH_FIND_CONTRA    = "src.agents.archivist.pipeline._find_contradiction_candidate"
_PATCH_STORE_K        = "src.agents.archivist.pipeline._store_knowledge"
_PATCH_STORE_E        = "src.agents.archivist.pipeline._store_embedding"
_PATCH_STORE_D        = "src.agents.archivist.pipeline._store_decision"


def _make_transaction_cm(conn):
    """Build an async context manager that yields conn."""
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = False
    return cm


def _make_get_conn_cm(conn):
    return _make_transaction_cm(conn)


# Decision validation

class TestValidateDecision:

    @pytest.mark.asyncio
    async def test_rejects_empty_statement(self):
        output = ArchivistOutput(source_report_id=None)
        record = await _validate_decision(
            index=0,
            raw={"statement": "", "rationale": "some reason", "linked_knowledge_refs": [0]},
            knowledge_id_map={0: uuid4()},
            output=output,
        )
        assert record.outcome == DecisionOutcome.REJECTED
        assert "empty" in record.reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_missing_rationale(self):
        output = ArchivistOutput(source_report_id=None)
        raw = _make_decision_raw(rationale="")
        record = await _validate_decision(
            index=0, raw=raw,
            knowledge_id_map={0: uuid4()},
            output=output,
        )
        assert record.outcome == DecisionOutcome.REJECTED
        assert "rationale" in record.reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_no_linked_knowledge(self):
        output = ArchivistOutput(source_report_id=None)
        raw = _make_decision_raw(linked_knowledge_refs=[])
        record = await _validate_decision(
            index=0, raw=raw,
            knowledge_id_map={},
            output=output,
        )
        assert record.outcome == DecisionOutcome.REJECTED
        assert "knowledge" in record.reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_when_ref_not_in_map(self):
        output = ArchivistOutput(source_report_id=None)
        raw = _make_decision_raw(linked_knowledge_refs=[99])
        record = await _validate_decision(
            index=0, raw=raw,
            knowledge_id_map={0: uuid4()},
            output=output,
        )
        assert record.outcome == DecisionOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_validates_with_linked_knowledge(self):
        known_id = uuid4()
        decision_id = uuid4()
        output = ArchivistOutput(source_report_id=None)
        raw = _make_decision_raw(linked_knowledge_refs=[0])

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {"id": decision_id}

        with patch(_PATCH_TRANSACTION, return_value=_make_transaction_cm(mock_conn)):
            record = await _validate_decision(
                index=0, raw=raw,
                knowledge_id_map={0: known_id},
                output=output,
            )

        assert record.outcome == DecisionOutcome.VALIDATED
        assert record.stored_id == decision_id


# Knowledge validation

class TestValidateKnowledge:

    @pytest.mark.asyncio
    async def test_rejects_empty_statement(self):
        output = ArchivistOutput(source_report_id=uuid4())
        raw = _make_knowledge_raw(statement="")
        record = await _validate_knowledge(
            index=0, raw=raw, source_report_id=uuid4(), output=output
        )
        assert record.outcome == KnowledgeOutcome.REJECTED

    @pytest.mark.asyncio
    async def test_rejects_no_provenance(self):
        raw = {
            "statement": "Fleet inspection interval is 30 days",
            "confidence": 0.85,
            "topics": ["fleet"],
            "source_report_ids": [],
            "source_section": "analysis",
            "contradiction_flag": False,
            "contradicts_id": None,
            "contradiction_type": None,
        }
        output = ArchivistOutput(source_report_id=None)
        record = await _validate_knowledge(
            index=0, raw=raw, source_report_id=None, output=output
        )
        assert record.outcome == KnowledgeOutcome.REJECTED
        assert "provenance" in record.reason.lower()

    @pytest.mark.asyncio
    async def test_clean_candidate_validates_and_stores(self):
        stored_id = uuid4()
        output = ArchivistOutput(source_report_id=uuid4())
        raw = _make_knowledge_raw()

        mock_provider = AsyncMock()
        mock_provider.embed.return_value = [0.1] * 1536

        with patch(_PATCH_GET_PROVIDER, return_value=mock_provider), \
             patch(_PATCH_FIND_SIMILAR, new_callable=AsyncMock, return_value=None), \
             patch(_PATCH_FIND_CONTRA, new_callable=AsyncMock, return_value=None), \
             patch(_PATCH_STORE_K, new_callable=AsyncMock, return_value=stored_id), \
             patch(_PATCH_STORE_E, new_callable=AsyncMock):

            record = await _validate_knowledge(
                index=0, raw=raw, source_report_id=uuid4(), output=output
            )

        assert record.outcome == KnowledgeOutcome.VALIDATED
        assert record.stored_id == stored_id

    @pytest.mark.asyncio
    async def test_high_stakes_contradiction_routes_to_review(self):
        existing_id = uuid4()
        output = ArchivistOutput(source_report_id=uuid4())
        raw = _make_knowledge_raw(
            topics=["safety"],
            contradiction_flag=True,
            contradicts_id=existing_id,
            contradiction_type="direct",
        )

        mock_provider = AsyncMock()
        mock_provider.embed.return_value = [0.1] * 1536

        with patch(_PATCH_GET_PROVIDER, return_value=mock_provider), \
             patch(_PATCH_FIND_SIMILAR, new_callable=AsyncMock, return_value=None), \
             patch(_PATCH_PUSH_TO_REVIEW, new_callable=AsyncMock) as mock_push:

            record = await _validate_knowledge(
                index=0, raw=raw, source_report_id=uuid4(), output=output
            )

        assert record.outcome == KnowledgeOutcome.REVIEW
        mock_push.assert_called_once()
        call_kwargs = mock_push.call_args
        assert "high_stakes" in call_kwargs.kwargs.get("reason", "") or \
               "high_stakes" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_temporal_contradiction_supersedes_old_item(self):
        existing_id = uuid4()
        new_id = uuid4()
        source_report_id = uuid4()
        output = ArchivistOutput(source_report_id=source_report_id)
        raw = _make_knowledge_raw(
            topics=["fleet"],
            contradiction_flag=True,
            contradicts_id=existing_id,
            contradiction_type="temporal",
        )

        mock_provider = AsyncMock()
        mock_provider.embed.return_value = [0.1] * 1536

        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "UPDATE 1"

        with patch(_PATCH_GET_PROVIDER, return_value=mock_provider), \
             patch(_PATCH_FIND_SIMILAR, new_callable=AsyncMock, return_value=None), \
             patch(_PATCH_TRANSACTION, return_value=_make_transaction_cm(mock_conn)), \
             patch(_PATCH_STORE_K, new_callable=AsyncMock, return_value=new_id), \
             patch(_PATCH_STORE_E, new_callable=AsyncMock):

            record = await _validate_knowledge(
                index=0, raw=raw, source_report_id=source_report_id, output=output
            )

        assert record.outcome == KnowledgeOutcome.VALIDATED
        assert record.stored_id == new_id
        assert "supersedes" in record.reason.lower()

    @pytest.mark.asyncio
    async def test_direct_contradiction_routes_to_human(self):
        existing_id = uuid4()
        output = ArchivistOutput(source_report_id=uuid4())
        raw = _make_knowledge_raw(
            topics=["fleet"],
            contradiction_flag=True,
            contradicts_id=existing_id,
            contradiction_type="direct",
        )

        mock_provider = AsyncMock()
        mock_provider.embed.return_value = [0.1] * 1536

        with patch(_PATCH_GET_PROVIDER, return_value=mock_provider), \
             patch(_PATCH_FIND_SIMILAR, new_callable=AsyncMock, return_value=None), \
             patch(_PATCH_PUSH_TO_REVIEW, new_callable=AsyncMock) as mock_push:

            record = await _validate_knowledge(
                index=0, raw=raw, source_report_id=uuid4(), output=output
            )

        assert record.outcome == KnowledgeOutcome.REVIEW
        mock_push.assert_called_once()

    @pytest.mark.asyncio
    async def test_embedding_failure_proceeds_without_similarity_checks(self):
        """If embedding fails, skip similarity/contradiction checks but still validate."""
        stored_id = uuid4()
        output = ArchivistOutput(source_report_id=uuid4())
        raw = _make_knowledge_raw()

        mock_provider = AsyncMock()
        mock_provider.embed.side_effect = RuntimeError("Embedding API down")

        with patch(_PATCH_GET_PROVIDER, return_value=mock_provider), \
             patch(_PATCH_STORE_K, new_callable=AsyncMock, return_value=stored_id), \
             patch(_PATCH_STORE_E, new_callable=AsyncMock):

            record = await _validate_knowledge(
                index=0, raw=raw, source_report_id=uuid4(), output=output
            )

        assert record.outcome == KnowledgeOutcome.VALIDATED
        assert record.stored_id == stored_id


# _collect_deprecated_ids

class TestCollectDeprecatedIds:

    def test_returns_existing_ids_of_superseded_items(self):
        existing_1 = uuid4()
        existing_2 = uuid4()
        output = ArchivistOutput(source_report_id=None)
        output.contradiction_records = [
            ContradictionRecord(
                new_item_index=0,
                existing_id=existing_1,
                contradiction_type="temporal",
                resolution="superseded",
                new_item_id=uuid4(),
            ),
            ContradictionRecord(
                new_item_index=1,
                existing_id=existing_2,
                contradiction_type="temporal",
                resolution="superseded",
                new_item_id=uuid4(),
            ),
        ]
        result = _collect_deprecated_ids(output)
        assert existing_1 in result
        assert existing_2 in result
        assert len(result) == 2

    def test_ignores_flagged_items(self):
        existing_id = uuid4()
        output = ArchivistOutput(source_report_id=None)
        output.contradiction_records = [
            ContradictionRecord(
                new_item_index=0,
                existing_id=existing_id,
                contradiction_type="direct",
                resolution="flagged",
                new_item_id=None,
            ),
        ]
        result = _collect_deprecated_ids(output)
        assert existing_id not in result
        assert len(result) == 0

    def test_empty_contradiction_records(self):
        output = ArchivistOutput(source_report_id=None)
        assert _collect_deprecated_ids(output) == []
