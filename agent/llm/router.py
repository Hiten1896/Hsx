import os

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
)
from .panel import PanelRouter
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

    def panel(self, provider_names: list[str]) -> PanelRouter | None:
        selected = [provider for provider in self.providers if provider.name in provider_names]
        if len(selected) < 2:
            return None
        judge = selected[2] if len(selected) >= 3 else selected[1]
        candidates = selected[:2]
        return PanelRouter(candidates, judge)


def default_router() -> LLMRouter:
    from config import (
        FALLBACKS,
        GEMINI_MODEL,
        GROK_MODEL,
        GROQ_MODEL,
        MODEL,
        OLLAMA_URL,
        OPENAI_COMPATIBLE_URL,
        OPENAI_MODEL,
        OPENROUTER_MODEL,
        PROVIDER,
    )

    # Read the current process environment on each construction so a caller can
    # configure or rotate keys without relying on a provider-specific default.
    keys = {
        "anthropic": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
        "grok": (os.getenv("GROK_API_KEY") or os.getenv("XAI_API_KEY") or "").strip(),
        "groq": os.getenv("GROQ_API_KEY", "").strip(),
        "openrouter": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "openai": os.getenv("OPENAI_API_KEY", "").strip(),
    }
    if not any(keys.values()):
        raise RuntimeError(
            "No valid API key found in .env. Please configure at least one provider key."
        )

    configured_names = [name for name in [PROVIDER, *FALLBACKS] if name]
    available_names = [name for name, key in keys.items() if key]
    names = list(dict.fromkeys([*configured_names, *available_names]))
    providers: list[Provider] = []
    for name in dict.fromkeys(names):
        if name == "anthropic" and keys["anthropic"]:
            providers.append(AnthropicProvider(keys["anthropic"], os.getenv("ANTHROPIC_MODEL", MODEL)))
        elif name == "gemini" and keys["gemini"]:
            providers.append(GeminiProvider(GEMINI_MODEL, f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={keys['gemini']}", keys["gemini"]))
        elif name in {"grok", "xai"} and keys["grok"]:
            providers.append(OpenAICompatibleProvider("grok", keys["grok"], GROK_MODEL, "https://api.x.ai/v1/chat/completions"))
        elif name == "groq" and keys["groq"]:
            providers.append(OpenAICompatibleProvider("groq", keys["groq"], GROQ_MODEL, "https://api.groq.com/openai/v1/chat/completions"))
        elif name == "openrouter" and keys["openrouter"]:
            providers.append(OpenAICompatibleProvider("openrouter", keys["openrouter"], OPENROUTER_MODEL, "https://openrouter.ai/api/v1/chat/completions"))
        elif name == "openai" and keys["openai"]:
            providers.append(OpenAICompatibleProvider("openai", keys["openai"], OPENAI_MODEL, "https://api.openai.com/v1/chat/completions"))
        elif name == "openai_compatible" and keys["openai"] and OPENAI_COMPATIBLE_URL:
            providers.append(OpenAICompatibleProvider("openai_compatible", keys["openai"], OPENAI_MODEL, OPENAI_COMPATIBLE_URL))
        elif name == "ollama" and (PROVIDER == "ollama" or "ollama" in FALLBACKS):
            providers.append(OllamaProvider(MODEL, f"{OLLAMA_URL.rstrip('/')}/api/chat"))
    return LLMRouter(providers)
