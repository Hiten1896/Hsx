# Making API keys last longer

What Hsx now does automatically, and the `.env` switches for it.

## Fewer tokens per request
| What | Default | Switch |
|---|---|---|
| **Tool selection.** Plain chat sends *no* tool schemas and a short system prompt. Action requests send only the matching tools (plus tools already used in the conversation). If the model replies `[[NEED_TOOLS]]` the turn is retried once with every tool. | on | `HSX_TOOL_SELECTION=0` sends everything again; `HSX_MAX_TOOLS=8` caps tools per request |
| **History trimming.** Old tool results and long text are shortened and the oldest whole turns are dropped (stored history is never changed). | 16000 est. tokens | `HSX_MAX_CONTEXT_TOKENS=0` turns it off |

Measured on the repo's 39 tools: all schemas ~3,400 tokens; a chat prompt now sends 0 of them, an action prompt sends ~8.

## Not hammering a limited provider
* A provider that answers 429 (or 401/403/5xx/unreachable) is paused for the time it asks for (`Retry-After`, Groq `x-ratelimit-reset-*`, Gemini `retryDelay`); otherwise 60 s, or 1 h when the message says "per day". Paused providers go to the back of the line but are still tried as a last resort. `/api/telemetry` shows them as `provider_cooldowns`.
* Optional client-side limits so Hsx waits instead of failing: `HSX_RATE_LIMITS="groq:rpm=30,tpm=8000;gemini:rpm=10"`. Copy the numbers from each provider's console; they change.

## Cloud providers only
Ollama / local models are no longer part of Hsx. Spread load with several cloud providers (each has its own free quota): the bandit and the cooldowns above move traffic to whichever one can answer. Per-provider limits are usually per project/organisation, so extra keys inside the same account do not add capacity.
