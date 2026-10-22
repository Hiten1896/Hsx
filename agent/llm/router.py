from config import ANTHROPIC_API_KEY, MODEL

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
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
    from config import (
        FALLBACKS,
        GEMINI_API_KEY,
        GEMINI_MODEL,
        GROQ_API_KEY,
        GROQ_MODEL,
        OLLAMA_URL,
        OPENAI_API_KEY,
        OPENAI_COMPATIBLE_URL,
        OPENAI_MODEL,
        OPENROUTER_API_KEY,
        OPENROUTER_MODEL,
        PROVIDER,
    )

    names = [PROVIDER, *FALLBACKS]
    providers = []
    for name in dict.fromkeys(names):
        if name == "anthropic" and ANTHROPIC_API_KEY:
            providers.append(AnthropicProvider(ANTHROPIC_API_KEY, MODEL))
        elif name == "gemini" and GEMINI_API_KEY:
            providers.append(GeminiProvider(GEMINI_MODEL, f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}", GEMINI_API_KEY))
        elif name == "groq" and GROQ_API_KEY:
            providers.append(OpenAICompatibleProvider("groq", GROQ_API_KEY, GROQ_MODEL, "https://api.groq.com/openai/v1/chat/completions"))
        elif name == "openrouter" and OPENROUTER_API_KEY:
            providers.append(OpenAICompatibleProvider("openrouter", OPENROUTER_API_KEY, OPENROUTER_MODEL, "https://openrouter.ai/api/v1/chat/completions"))
        elif name == "openai" and OPENAI_API_KEY:
            providers.append(OpenAICompatibleProvider("openai", OPENAI_API_KEY, OPENAI_MODEL, "https://api.openai.com/v1/chat/completions"))
        elif name == "openai_compatible" and OPENAI_API_KEY and OPENAI_COMPATIBLE_URL:
            providers.append(OpenAICompatibleProvider("openai_compatible", OPENAI_API_KEY, OPENAI_MODEL, OPENAI_COMPATIBLE_URL))
        elif name == "ollama":
            providers.append(OllamaProvider(MODEL, f"{OLLAMA_URL.rstrip('/')}/api/chat"))
    return LLMRouter(providers)
