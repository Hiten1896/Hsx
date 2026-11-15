import os
import logging
import time

from .providers import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
)
from .ratelimit import BUDGET, COOLDOWNS, RateBudget, ProviderCooldowns, cooldown_for_error, estimate_request_tokens, is_quota_error
from .types import EventStream, LLMEvent, LLMRequest

LOGGER = logging.getLogger("hsx.llm.router")


MAX_BUDGET_WAIT_SECONDS = 2.0


class LLMRouter:
    def __init__(
        self,
        providers: list[Provider],
        cooldowns: ProviderCooldowns | None = None,
        budget: RateBudget | None = None,
    ):
        self.providers = providers
        self.cooldowns = cooldowns if cooldowns is not None else COOLDOWNS
        self.budget = budget if budget is not None else BUDGET
        self.active_provider = "unknown"
        self.active_model = "unknown"
        self.provider_result_callback = None
        self.last_result = {"status": "Standby", "reason": ""}

    def _ordered(self, tokens: int) -> list[Provider]:
        """Providers that can take a request now come first; cooled-down or over-budget ones are the last resort."""
        ready: list[Provider] = []
        waiting: list[tuple[float, Provider]] = []
        for provider in self.providers:
            cooling = self.cooldowns.remaining(provider.name)
            over_budget = self.budget.wait_needed(provider.name, tokens)
            if cooling > 0 or over_budget > 0:
                waiting.append((max(cooling, over_budget), provider))
            else:
                ready.append(provider)
        waiting.sort(key=lambda item: item[0])
        return [*ready, *(provider for _, provider in waiting)]

    def _note_failure(self, provider: Provider, error: BaseException) -> None:
        seconds, reason = cooldown_for_error(error)
        if seconds > 0:
            self.cooldowns.mark(provider.name, seconds, reason)
            LOGGER.warning("LLM provider %s is paused for %.0f s (%s).", provider.name, seconds, reason)

    def _start(self, provider: Provider, request: LLMRequest):
        """Run a provider. Streaming providers are consumed live once their first event arrives."""
        if request.stream and getattr(provider, "streams", False):
            iterator = iter(provider.generate(request))
            first = next(iterator, None)  # a failure before any output still falls back to the next provider

            def chained():
                if first is not None:
                    yield first
                yield from iterator

            return chained(), True
        # Commit a provider's stream only after it completes. A quota
        # error can arrive after initial chunks; exposing those chunks
        # would mix the failed response with the fallback response.
        return list(provider.generate(request)), False

    def generate(self, request: LLMRequest) -> EventStream:
        errors = []
        self.last_result = {"status": "Standby", "reason": ""}
        tokens = estimate_request_tokens(request)
        ordered = self._ordered(tokens)
        for index, provider in enumerate(ordered):
            wait = self.budget.wait_needed(provider.name, tokens)
            if 0 < wait <= MAX_BUDGET_WAIT_SECONDS:
                time.sleep(wait)
            self.active_provider = provider.name
            self.active_model = getattr(provider, "model", "unknown")
            started = time.perf_counter()
            try:
                provider_events, live = self._start(provider, request)
            except Exception as error:
                self._note_failure(provider, error)
                if self.provider_result_callback is not None:
                    self.provider_result_callback(
                        provider.name,
                        False,
                        (time.perf_counter() - started) * 1000,
                    )
                is_quota = is_quota_error(error)
                if is_quota and provider is not ordered[-1]:
                    LOGGER.warning(
                        "LLM provider %s hit a rate limit or quota; falling back to the next configured provider",
                        provider.name,
                    )
                else:
                    LOGGER.warning("LLM provider %s failed: %s", provider.name, error)
                errors.append((provider.name, str(error)))
                if index + 1 < len(ordered):
                    next_provider = ordered[index + 1]
                    yield LLMEvent(
                        type="provider_notice",
                        text=f"{provider.name.title()} is unavailable; trying {next_provider.name.title()}.",
                    )
            else:
                if errors:
                    yield LLMEvent(type="provider_notice_clear")
                if live:
                    try:
                        yield from provider_events
                    except Exception as error:
                        # Text is already on screen, so a mid-answer failure cannot fall back cleanly.
                        self._note_failure(provider, error)
                        if self.provider_result_callback is not None:
                            self.provider_result_callback(provider.name, False, (time.perf_counter() - started) * 1000)
                        raise ProviderError(
                            f"{provider.name.title()} stopped mid-answer: {error}", provider=provider.name
                        ) from error
                else:
                    yield from provider_events
                self.budget.record(provider.name, tokens)
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
    if not keys:
        raise RuntimeError(
            "No LLM provider is configured. Set a Gemini, Groq, OpenRouter, OpenAI, Anthropic or Grok API key."
        )

    configured = [value("HSX_PROVIDER"), *value("HSX_FALLBACKS").split(",")]
    discovered = ["gemini", "groq", "grok", "anthropic", "openrouter", "openai"]
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
        elif name == "openrouter" and keys.get(name):
            openrouter_model = model_setting("OPENROUTER_MODEL", "google/gemini-3.6-flash")
            providers.append(OpenAICompatibleProvider("openrouter", keys[name], openrouter_model, "https://openrouter.ai/api/v1/chat/completions"))
        elif name == "openai" and keys.get(name):
            providers.append(OpenAICompatibleProvider("openai", keys[name], value("OPENAI_MODEL") or "gpt-4o-mini", "https://api.openai.com/v1/chat/completions"))
        elif name == "openai_compatible" and keys.get("openai") and value("OPENAI_COMPATIBLE_URL"):
            providers.append(OpenAICompatibleProvider("openai_compatible", keys["openai"], value("OPENAI_MODEL") or "gpt-4o-mini", value("OPENAI_COMPATIBLE_URL")))
    return LLMRouter(providers)
