import os
import logging
import time

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
)
from .panel import PanelRouter
from .types import EventStream, LLMRequest

LOGGER = logging.getLogger("hsx.llm.router")


class LLMRouter:
    def __init__(self, providers: list[Provider]):
        self.providers = providers
        self.active_provider = "unknown"
        self.active_model = "unknown"
        self.provider_result_callback = None
        self.last_result = {"status": "Standby", "reason": ""}

    def generate(self, request: LLMRequest) -> EventStream:
        errors = []
        self.last_result = {"status": "Standby", "reason": ""}
        for provider in self.providers:
            self.active_provider = provider.name
            self.active_model = getattr(provider, "model", "unknown")
            started = time.perf_counter()
            try:
                # Commit a provider's stream only after it completes. A quota
                # error can arrive after initial chunks; exposing those chunks
                # would mix the failed response with the fallback response.
                provider_events = list(provider.generate(request))
            except Exception as error:
                if self.provider_result_callback is not None:
                    self.provider_result_callback(
                        provider.name,
                        False,
                        (time.perf_counter() - started) * 1000,
                    )
                status = getattr(error, "status", None)
                is_quota = status == 429 or any(
                    marker in str(error).casefold()
                    for marker in ("quota", "rate limit", "resource_exhausted", "too many requests")
                )
                if is_quota and provider is not self.providers[-1]:
                    LOGGER.warning(
                        "LLM provider %s hit a rate limit or quota; falling back to the next configured provider",
                        provider.name,
                    )
                else:
                    LOGGER.warning("LLM provider %s failed: %s", provider.name, error)
                errors.append((provider.name, str(error)))
            else:
                yield from provider_events
                if self.provider_result_callback is not None:
                    self.provider_result_callback(
                        provider.name,
                        True,
                        (time.perf_counter() - started) * 1000,
                    )
                self.last_result = {
                    "status": f"Fallback to {provider.name.title()}" if errors else "Used",
                    "reason": errors[0][1] if errors else "",
                }
                return
        if not errors:
            raise ProviderError("No LLM provider is available. Configure a provider in .env.")
        if len(errors) == 1:
            provider_name, message = errors[0]
            short_message = message.replace("\n", " ")[:72]
            self.last_result = {
                "status": f"Failed ({provider_name.title()}: {short_message})",
                "reason": message,
            }
            normalized = message.casefold()
            if "rejected the conversation format" not in normalized and "model not found" not in normalized:
                raise ProviderError(message, provider=provider_name)
        elif errors:
            provider_name, message = errors[-1]
            short_message = message.replace("\n", " ")[:72]
            self.last_result = {
                "status": f"Failed ({provider_name.title()}: {short_message})",
                "reason": message,
            }
        provider_names = ", ".join(dict.fromkeys(name.title() for name, _ in errors))
        reasons = {message for _, message in errors}
        gemini_failure = next((message for name, message in errors if name.casefold() == "gemini"), None)
        if gemini_failure:
            reason = gemini_failure.splitlines()[0]
            fallback_failure = next(
                (message for name, message in errors if name.casefold() == "groq"),
                "The configured fallback providers also failed.",
            )
            action = f"Groq fallback did not complete: {fallback_failure[:100]}"
        elif any("model not found" in reason.casefold() for reason in reasons):
            reason = "The configured provider model was not found."
            action = "Refresh the provider model list and set a valid model in .env."
        else:
            reason = "The configured language model providers could not complete the request."
            action = "Check provider availability and settings, then try again."
        raise ProviderError(
            f"Provider failure: {provider_names}.\n{reason}\nWhat to do: {action}",
            provider=provider_names,
        )

    def panel(self, provider_names: list[str]) -> PanelRouter | None:
        selected = [provider for provider in self.providers if provider.name in provider_names]
        if len(selected) < 2:
            return None
        judge = selected[2] if len(selected) >= 3 else selected[1]
        candidates = selected[:2]
        return PanelRouter(candidates, judge)


def default_router() -> LLMRouter:
    from config import ENV_FILE, model_setting
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE, override=False)
    def value(name: str) -> str:
        return os.getenv(name, "").strip()
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
    ollama_model = value("OLLAMA_MODEL")
    if not keys and not ollama_model:
        raise RuntimeError(
            "No LLM provider is configured. Set a Gemini/Groq API key or configure OLLAMA_MODEL."
        )

    configured = [value("HSX_PROVIDER"), *value("HSX_FALLBACKS").split(",")]
    discovered = ["gemini", "groq", "ollama", "grok", "anthropic", "openrouter", "openai"]
    names = list(dict.fromkeys(name.lower() for name in [*configured, *discovered] if name.strip()))
    providers: list[Provider] = []
    model = value("HSX_MODEL") or "claude-sonnet-4-6"
    for name in dict.fromkeys(names):
        if name == "anthropic" and keys.get(name):
            providers.append(AnthropicProvider(keys[name], model))
        elif name == "gemini" and keys.get(name):
            gemini_model = model_setting("GEMINI_MODEL", "gemini-3.6-flash")
            providers.append(GeminiProvider(gemini_model, f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent", keys[name]))
        elif name in {"grok", "xai"} and keys.get("grok"):
            grok_model = value("GROK_MODEL") or value("XAI_MODEL") or "grok-3-mini"
            providers.append(OpenAICompatibleProvider("grok", keys["grok"], grok_model, "https://api.x.ai/v1/chat/completions"))
        elif name == "groq" and keys.get(name):
            try:
                providers.append(GroqProvider(keys[name], value("GROQ_MODEL")))
            except ProviderError as error:
                LOGGER.warning("Groq provider was not initialized: %s", error)
        elif name == "ollama" and ollama_model:
            from config import OLLAMA_URL

            providers.append(OllamaProvider(ollama_model, f"{OLLAMA_URL.rstrip('/')}/v1/chat/completions"))
        elif name == "openrouter" and keys.get(name):
            openrouter_model = model_setting("OPENROUTER_MODEL", "google/gemini-3.6-flash")
            providers.append(OpenAICompatibleProvider("openrouter", keys[name], openrouter_model, "https://openrouter.ai/api/v1/chat/completions"))
        elif name == "openai" and keys.get(name):
            providers.append(OpenAICompatibleProvider("openai", keys[name], value("OPENAI_MODEL") or "gpt-4o-mini", "https://api.openai.com/v1/chat/completions"))
        elif name == "openai_compatible" and keys.get("openai") and value("OPENAI_COMPATIBLE_URL"):
            providers.append(OpenAICompatibleProvider("openai_compatible", keys["openai"], value("OPENAI_MODEL") or "gpt-4o-mini", value("OPENAI_COMPATIBLE_URL")))
    return LLMRouter(providers)
