"""Context — moved to ``las.modules.context`` (ADR-0004).

Compatibility re-export at the pre-migration path; removed in migration
step S7 (ADR-0004).
"""

from las.modules.context import (  # noqa: F401
    AssembledContext, COMPACT_KEEP_FACTS, COMPACT_SUMMARY_WORDS,
    CONTEXT_WARNING_RATIO, MANIFEST_COMPACT, MAX_OUTPUT_TOKENS_DEFAULT,
    ROLE_PROMPTS, SAFETY_MARGIN, TEXT_TOOL_PROTOCOL, TOOL_CALL_RE, UNTRUSTED_LABEL,
    WORKER_PROTOCOL, assemble_context, estimate_tokens, parse_text_tool_call,
)

__all__ = [
    "AssembledContext", "TEXT_TOOL_PROTOCOL", "WORKER_PROTOCOL",
    "assemble_context", "estimate_tokens", "parse_text_tool_call",
]
