from agent.ask_user import AskUserRequest


def run(
    question: str,
    options: list[str] | None = None,
    allow_free_text: bool = True,
    timeout_hours: int = 24,
) -> None:
    raise AskUserRequest(question, options, allow_free_text, timeout_hours)
