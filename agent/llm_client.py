from config import ANTHROPIC_API_KEY, MODEL


def call_llm(messages: list, tools: list):
    if not ANTHROPIC_API_KEY:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=(
            "You are Hsx, a concise and capable local workspace agent. "
            "Only call a tool when the request requires file generation, file reading, "
            "or code execution. Otherwise answer directly. Make sensible assumptions "
            "instead of asking unnecessary questions. Generated files belong in outputs; "
            "intermediate work belongs in scratch; never modify uploads."
        ),
        messages=messages,
        tools=tools,
    )