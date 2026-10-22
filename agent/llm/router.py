from config import ANTHROPIC_API_KEY, MODEL

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    Provider,
    ProviderError,
)
from .types import EventStream, LLMRequest


class LLMRouter:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    def generate(self, request: LLMRequest) -> EventStream:
        errors = []
        for provider in self.providers:
            try:
                yield from provider.generate(request)
                return
            except (ProviderError, OSError, TimeoutError) as error:
                errors.append(f"{provider.name}: {error}")
        raise ProviderError("No LLM provider succeeded: " + "; ".join(errors))


def default_router() -> LLMRouter:
    from config import FALLBACKS, GEMINI_API_KEY, OLLAMA_URL, PROVIDER

    names = [PROVIDER, *FALLBACKS]
    providers = []
    for name in dict.fromkeys(names):
        if name == "anthropic" and ANTHROPIC_API_KEY:
            providers.append(AnthropicProvider(ANTHROPIC_API_KEY, MODEL))
        elif name == "gemini" and GEMINI_API_KEY:
            providers.append(GeminiProvider(MODEL, f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_API_KEY}", GEMINI_API_KEY))
        elif name == "ollama":
            providers.append(OllamaProvider(MODEL, f"{OLLAMA_URL.rstrip('/')}/api/chat"))
    return LLMRouter(providers)
