from agent.subagents import delegate


def test_subagent_depth_and_result_boundary():
    def runner(name, args):
        return {"tool": name}

    assert delegate("researcher", "find facts", runner)["untrusted"]
    assert delegate("researcher", "too deep", runner, depth=2)["success"] is False
