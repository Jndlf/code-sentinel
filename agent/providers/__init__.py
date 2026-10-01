from __future__ import annotations

from typing import Any

from ..config import ModelProfile
from .base import Conversation


def make_conversation(profile: ModelProfile, system: str, tools: list[dict[str, Any]],
                      max_tokens: int) -> Conversation:
    profile.validate()
    if profile.provider == "anthropic":
        from .anthropic_conv import AnthropicConversation
        return AnthropicConversation(profile, system, tools, max_tokens)
    if profile.provider == "openai-responses":
        from .openai_responses_conv import OpenAIResponsesConversation
        return OpenAIResponsesConversation(profile, system, tools, max_tokens)
    from .openai_conv import OpenAIConversation
    return OpenAIConversation(profile, system, tools, max_tokens)
