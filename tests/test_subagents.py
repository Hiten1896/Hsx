from agent.subagents import delegate


def test_subagent_depth_and_result_boundary():
    runner = lambda name, args: {"tool": name}
    assert delegate("researcher", "find facts", runner)["untrusted"]
    assert delegate("researcher", "too deep", runner, depth=2)["success"] is False
