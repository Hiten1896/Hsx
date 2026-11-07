from agent.system_tools import create_text_file


def run(path: str, content: str):
    result = create_text_file(path, content)
    if result.get("success") is True:
        result["artifact"] = True
    return result
