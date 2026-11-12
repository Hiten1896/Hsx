import json
import subprocess
from pathlib import Path
from tools.lab_sandbox import LAB_ROOT, LabSandbox

MCP_REGISTRY_PATH = Path.home() / "hsx" / "data" / "mcp_servers.json"
MCP_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

def ingest_github_repo(repo_slug: str) -> dict:
    """Clones a GitHub repository, inspects its layout, and registers it as an HSX capability."""
    repo_name = repo_slug.split("/")[-1]
    target_dir = LAB_ROOT / repo_name
    
    # 1. Shallow clone into lab_repos
    if not target_dir.exists():
        clone_res = subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo_slug}.git", str(target_dir)],
            capture_output=True, text=True, check=False
        )
        if clone_res.returncode != 0:
            return {"success": False, "error": f"Git clone failed: {clone_res.stderr}"}

    # 2. Inspect structure to determine integration pattern
    has_package_json = (target_dir / "package.json").exists()
    has_pyproject = (target_dir / "pyproject.toml").exists()
    
    integration_type = "cli"
    if has_package_json:
        # Install globally or run via npx
        install_res = subprocess.run(
            ["npm", "install", "--prefix", str(target_dir)],
            capture_output=True, text=True, check=False
        )
        integration_type = "mcp_node" if "mcp" in target_dir.read_text(errors="ignore").lower() else "node_cli"
    
    # 3. Register into MCP/Tool config
    registry = {}
    if MCP_REGISTRY_PATH.exists():
        try:
            registry = json.loads(MCP_REGISTRY_PATH.read_text())
        except Exception:
            pass
            
    registry[repo_name] = {
        "slug": repo_slug,
        "path": str(target_dir),
        "type": integration_type
    }
    MCP_REGISTRY_PATH.write_text(json.dumps(registry, indent=2))

    return {
        "success": True,
        "message": f"Successfully ingested {repo_slug} into AI Lab storage.",
        "repo": repo_name,
        "type": integration_type
    }