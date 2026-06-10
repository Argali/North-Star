"""
System prompts and Anthropic tool schemas for the Scribe pipeline.

Two LLM calls per pipeline run:
  1. generate_report  → ReportDraft (title, context_summary, analysis, conclusions, tags)
  2. extract_candidates → ExtractionResult (knowledge, decisions, relationships)

Using Anthropic tool_use for both calls ensures deterministic JSON structure
regardless of model verbosity. The model is forced to call the tool.
"""

# ── System prompt ─────────────────────────────────────────────────────────────

SCRIBE_SYSTEM_PROMPT = """\
You are the Scribe agent for North Star, an institutional memory system.

Your purpose is to transform raw activity (conversations, logs, documents) into
structured, durable organizational artifacts. You extract only what is explicitly
present in the source — you never invent, infer beyond evidence, or speculate.

Core rules:
1. ACCURACY ABOVE ALL — every fact must be present in the source material.
   If you are uncertain, set confidence low and fill uncertainties honestly.
2. ATOMIC STATEMENTS — one fact per knowledge item. Never compound.
3. EVIDENCE-BASED — every knowledge item must include a source_excerpt.
4. NON-SPECULATIVE — uncertainty belongs in the `uncertainties` field,
   not in the statement. Statements are declarative facts only.
5. SCOPE MATTERS — if a fact is conditional or domain-specific,
   state the scope_conditions explicitly.
6. DECISIONS REQUIRE EVIDENCE — do not extract a decision unless it is
   explicitly stated as a choice, commitment, or action in the source.
7. COMPRESSION BUDGET — for safety, compliance, or financial content:
   no knowledge item without a verbatim source excerpt.

You do not curate, validate, or resolve contradictions. That is the Archivist's job.
When in doubt, extract and flag rather than discard.\
"""

# ── Tool: generate_report ─────────────────────────────────────────────────────

REPORT_TOOL: dict = {
    "name": "generate_report",
    "description": (
        "Generate a structured, human-readable report from the raw input. "
        "Summarise what happened, what was examined, and what was found or decided. "
        "The report is immutable evidence — write it to be understandable without the source."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Concise, descriptive title. Identifies the subject and timeframe. "
                    "Example: 'Q2 2026 Fleet Maintenance Review' or "
                    "'TargetCross Integration Assessment — June 2026'."
                ),
            },
            "context_summary": {
                "type": "string",
                "description": (
                    "What was the situation? "
                    "2–4 sentences covering: who was involved, what triggered this activity, "
                    "and what the scope was. No conclusions here."
                ),
            },
            "analysis": {
                "type": "string",
                "description": (
                    "What was examined and how? "
                    "Describe the reasoning process, data reviewed, and key observations. "
                    "Stay close to the source material."
                ),
            },
            "conclusions": {
                "type": "string",
                "description": (
                    "What was found or decided? "
                    "State the outcomes, findings, and any explicit decisions made. "
                    "Be specific. Avoid vague summaries."
                ),
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Topic classification tags. Use lowercase slugs separated by hyphens. "
                    "Examples: ['fleet-maintenance', 'cost-analysis', 'vehicle-259', 'q2-2026']. "
                    "2–6 tags per report."
                ),
            },
        },
        "required": ["title", "context_summary", "analysis", "conclusions", "tags"],
    },
}

# ── Tool: extract_candidates ──────────────────────────────────────────────────

EXTRACT_TOOL: dict = {
    "name": "extract_candidates",
    "description": (
        "Extract knowledge candidates, decision candidates, and relationship candidates "
        "from the report and its source. "
        "Only extract what is explicitly present. Never invent or infer beyond the evidence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {

            # ── Knowledge candidates ──────────────────────────────────────────
            "knowledge_candidates": {
                "type": "array",
                "description": (
                    "Atomic, declarative facts extracted from the source. "
                    "Each item must satisfy ALL rules: "
                    "(1) one fact only, (2) declarative not speculative, "
                    "(3) has a source_excerpt, (4) scoped if conditional."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {
                            "type": "string",
                            "description": (
                                "One declarative fact. GOOD: 'Vehicle 259 exceeded maintenance cost "
                                "threshold in Q2 2026.' BAD: 'Vehicle 259 is probably failing.' "
                                "(speculative) or 'Vehicle 259 has high costs and issues.' (compound)"
                            ),
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": (
                                "Your confidence that this statement is accurate. "
                                "Use 0.9–1.0 for clear, directly stated facts. "
                                "Use 0.5–0.8 for facts that require interpretation. "
                                "Use 0.1–0.4 for facts that depend on implicit context. "
                                "Use 0.0 if extraction is uncertain — do not suppress, flag instead."
                            ),
                        },
                        "scope_conditions": {
                            "type": "string",
                            "description": (
                                "When and where this fact applies. "
                                "Example: 'Applies to vehicles in the northern fleet as of Q2 2026.' "
                                "If universal, write 'No known scope restrictions.'"
                            ),
                        },
                        "uncertainties": {
                            "type": "string",
                            "description": (
                                "What this fact does NOT account for. "
                                "Example: 'Does not account for planned maintenance cycles or "
                                "seasonal cost variation.' "
                                "If none, write 'None identified.'"
                            ),
                        },
                        "source_excerpt": {
                            "type": "string",
                            "description": (
                                "Verbatim or near-verbatim text from the source that supports "
                                "this statement. Required for all high-stakes domains. "
                                "Must be traceable — do not paraphrase beyond recognition."
                            ),
                        },
                        "source_section": {
                            "type": "string",
                            "description": (
                                "Reference to where in the report this comes from. "
                                "Use the report section name or a short descriptor: "
                                "'context_summary', 'analysis', 'conclusions', "
                                "or a paragraph marker like 'analysis:para-2'."
                            ),
                        },
                        "topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "1–4 lowercase slug tags. Should overlap with report tags "
                                "where relevant. Example: ['fleet-maintenance', 'vehicle-259']."
                            ),
                        },
                    },
                    "required": [
                        "statement",
                        "confidence",
                        "scope_conditions",
                        "uncertainties",
                        "source_excerpt",
                        "source_section",
                        "topics",
                    ],
                },
            },

            # ── Decision candidates ───────────────────────────────────────────
            "decision_candidates": {
                "type": "array",
                "description": (
                    "Explicit organisational choices, commitments, or actions. "
                    "Only extract if clearly stated, not implied. "
                    "Must be traceable to at least one knowledge candidate."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {
                            "type": "string",
                            "description": (
                                "What was decided. Active voice. "
                                "GOOD: 'Vehicle 259 will be sold.' "
                                "BAD: 'We should look into the vehicle situation.' (not a decision)"
                            ),
                        },
                        "rationale": {
                            "type": "string",
                            "description": (
                                "Why this decision was made. "
                                "Must reference specific evidence from the source."
                            ),
                        },
                        "linked_knowledge_refs": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "0-based indexes into knowledge_candidates that support this decision. "
                                "Example: [0, 2] means knowledge_candidates[0] and [2] justify this. "
                                "Must have at least one reference."
                            ),
                        },
                        "owner": {
                            "type": "string",
                            "description": (
                                "Who made or owns this decision. "
                                "Use the name or role from the source. "
                                "If unspecified, use null."
                            ),
                        },
                        "status": {
                            "type": "string",
                            "enum": ["planned", "executed"],
                            "description": (
                                "'executed' if the action has already happened. "
                                "'planned' if it is a commitment for the future."
                            ),
                        },
                    },
                    "required": ["statement", "rationale", "linked_knowledge_refs", "status"],
                },
            },

            # ── Relationship candidates ───────────────────────────────────────
            "relationship_candidates": {
                "type": "array",
                "description": (
                    "Graph edges linking nodes. "
                    "Ref system: 'report' = this report, "
                    "'K0'...'KN' = knowledge_candidates[N], "
                    "'D0'...'DN' = decision_candidates[N], "
                    "'E:<name>' = entity to resolve by name (e.g. 'E:Vehicle 259')."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "from_ref": {
                            "type": "string",
                            "description": "Source node ref. Example: 'report', 'K0', 'D0', 'E:Vehicle 259'.",
                        },
                        "to_ref": {
                            "type": "string",
                            "description": "Target node ref. Same format as from_ref.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["supports", "informs", "contradicts", "relates_to"],
                            "description": (
                                "supports   : report → knowledge (report is evidence for the fact) | "
                                "informs    : knowledge → decision (fact supports the decision) | "
                                "contradicts: knowledge ↔ knowledge (conflicting claims) | "
                                "relates_to : knowledge/decision → entity (domain object link)"
                            ),
                        },
                    },
                    "required": ["from_ref", "to_ref", "type"],
                },
            },
        },
        "required": ["knowledge_candidates", "decision_candidates", "relationship_candidates"],
    },
}

# ── User prompt templates ─────────────────────────────────────────────────────

def report_generation_prompt(source_type: str, text: str, author: str | None) -> str:
    """Build the user message for the report generation call."""
    author_line = f"Author: {author}" if author else "Author: unknown"
    return f"""\
Source type: {source_type}
{author_line}

--- SOURCE MATERIAL ---
{text}
--- END SOURCE ---

Generate a structured report from this source material.
Call the generate_report tool with the result.\
"""


def extraction_prompt(report_title: str, report_text: str, source_text: str) -> str:
    """Build the user message for the candidate extraction call."""
    return f"""\
Report title: {report_title}

--- REPORT ---
{report_text}
--- END REPORT ---

--- ORIGINAL SOURCE ---
{source_text}
--- END SOURCE ---

Extract all knowledge candidates, decision candidates, and relationship candidates.
Call the extract_candidates tool with the result.

Strict rules:
- knowledge statements must be atomic and declarative
- decisions must be explicit (not implied), with rationale and at least one linked_knowledge_ref
- relationship refs must use the ref system: 'report', 'K0'...'KN', 'D0'...'DN', 'E:<EntityName>'
- if you cannot find valid candidates of a type, return an empty array for that type\
"""
