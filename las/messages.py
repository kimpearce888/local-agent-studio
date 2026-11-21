"""Message catalogue (spec section 8) — moved to ``las.shared.messages``.

This module remains as a compatibility re-export during the modular
migration (ADR-0004, step S7 removes it). Import from ``las.shared``
in new code.
"""

from las.shared.messages import MESSAGES, message, message_with_fix

__all__ = ["MESSAGES", "message", "message_with_fix"]
