"""Wire adapters — moved to ``las.modules.providers.openai_compat`` (ADR-0004).

Compatibility re-export at the pre-migration path; step S7 removes it.
"""

from las.modules.providers import ChatCompletionsAdapter, ResponsesAdapter, resolve_api_key

__all__ = ["ChatCompletionsAdapter", "ResponsesAdapter", "resolve_api_key"]
