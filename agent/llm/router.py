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
            except Exception as error:
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
    from config import ENV_FILE
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)
    value = lambda name: os.getenv(name, "").strip()
    keys = {
        "gemini": value("GEMINI_API_KEY"),
        "grok": value("GROK_API_KEY") or value("XAI_API_KEY"),
        "anthropic": value("ANTHROPIC_API_KEY"),
        "groq": value("GROQ_API_KEY"),
        "openrouter": value("OPENROUTER_API_KEY"),
        "openai": value("OPENAI_API_KEY"),
    }
    invalid_placeholders = {"your_key_here", "your_api_key", "changeme"}
    keys = {name: key for name, key in keys.items() if key and key.lower() not in invalid_placeholders}
    if not keys:
        raise RuntimeError("No valid API key found in .env. Please configure at least one provider key.")

    configured = [value("HSX_PROVIDER"), *value("HSX_FALLBACKS").split(",")]
    discovered = ["gemini", "grok", "anthropic", "groq", "openrouter", "openai"]
    names = list(dict.fromkeys(name.lower() for name in [*configured, *discovered] if name.strip()))
    providers: list[Provider] = []
    model = value("HSX_MODEL") or "claude-sonnet-4-6"
    for name in dict.fromkeys(names):
        if name == "anthropic" and keys.get(name):
            providers.append(AnthropicProvider(keys[name], model))
        elif name == "gemini" and keys.get(name):
            gemini_model = value("GEMINI_MODEL") or "gemini-2.0-flash"
            providers.append(GeminiProvider(gemini_model, f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={keys[name]}", keys[name]))
        elif name in {"grok", "xai"} and keys.get("grok"):
            grok_model = value("GROK_MODEL") or value("XAI_MODEL") or "grok-3-mini"
            providers.append(OpenAICompatibleProvider("grok", keys["grok"], grok_model, "https://api.x.ai/v1/chat/completions"))
        elif name == "groq" and keys.get(name):
            providers.append(OpenAICompatibleProvider("groq", keys[name], value("GROQ_MODEL") or "llama-3.1-8b-instant", "https://api.groq.com/openai/v1/chat/completions"))
        elif name == "openrouter" and keys.get(name):
            providers.append(OpenAICompatibleProvider("openrouter", keys[name], value("OPENROUTER_MODEL") or "google/gemini-2.0-flash-exp:free", "https://openrouter.ai/api/v1/chat/completions"))
        elif name == "openai" and keys.get(name):
            providers.append(OpenAICompatibleProvider("openai", keys[name], value("OPENAI_MODEL") or "gpt-4o-mini", "https://api.openai.com/v1/chat/completions"))
        elif name == "openai_compatible" and keys.get("openai") and value("OPENAI_COMPATIBLE_URL"):
            providers.append(OpenAICompatibleProvider("openai_compatible", keys["openai"], value("OPENAI_MODEL") or "gpt-4o-mini", value("OPENAI_COMPATIBLE_URL")))
    return LLMRouter(providers)
