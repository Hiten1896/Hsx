from agent.improvement import ImprovementProposal, ImprovementValidator


def run(name: str, rationale: str, files: dict, tests: dict) -> dict:
    try:
        proposal = ImprovementProposal.from_dict(
            {"name": name, "rationale": rationale, "files": files, "tests": tests}
        )
        return {"success": True, "report": ImprovementValidator().validate(proposal)}
    except (OSError, ValueError) as error:
        return {"success": False, "error": str(error)}
