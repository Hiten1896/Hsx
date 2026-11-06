from agent.system_tools import create_text_file


def run(path: str, content: str):
    return create_text_file(path, content)
