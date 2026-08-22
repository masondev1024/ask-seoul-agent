"""Provider-neutral interfaces for model adapters."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ask_seoul_agent.models import ModelTurn

type Message = Mapping[str, Any]
type ToolSchema = Mapping[str, Any]


@dataclass(slots=True)
class ProviderError(Exception):
    """Sanitized provider failure safe for API responses and logs."""

    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class LLMProvider(Protocol):
    """Vendor-neutral model completion port used by the agent runner."""

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        *,
        timeout_s: float,
    ) -> ModelTurn:
        """Return a final model turn or a set of structured tool calls."""
