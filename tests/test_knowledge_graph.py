import json

from agent.memory.knowledge_graph import KnowledgeGraph


def test_knowledge_graph_indexes_symbols_imports_and_markdown_links(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "helper.py").write_text(
        "def parse_config():\n    return {}\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(
        "from helper import parse_config\n"
        "class Agent:\n"
        "    def run(self):\n"
        "        return parse_config()\n",
        encoding="utf-8",
    )
    (root / "guide.md").write_text("[implementation](main.py)\n", encoding="utf-8")
    graph_path = tmp_path / "knowledge_graph.json"
    graph = KnowledgeGraph(root, graph_path)

    assert graph.build() >= 6
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    relations = {edge["attributes"]["relation"] for edge in payload["edges"]}
    assert {"defines", "contains", "imports", "calls", "links"} <= relations
    results = graph.retrieve("Agent run parser_config")
    assert any(item["path"] == "main.py" for item in results)
    context = graph.prompt_context("Agent run")
    assert "hsx-untrusted-knowledge-graph" in context
    assert "main.py" in context


def test_knowledge_graph_enforces_bounds_and_reloads(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "sample.py").write_text("def run():\n    return True\n", encoding="utf-8")
    path = tmp_path / "graph.json"
    first = KnowledgeGraph(root, path)
    first.build()

    restored = KnowledgeGraph(root, path)
    assert restored.retrieve("run")[0]["path"] == "sample.py"
