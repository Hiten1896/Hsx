import os
import urllib.parse
import urllib.request


def run(query: str) -> dict:
    endpoint = os.getenv("HSX_SEARCH_URL", "").strip()
    if not endpoint:
        return {"success": False, "error": "HSX_SEARCH_URL is not configured."}
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode({"q": query})
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return {"success": True, "results": response.read(256 * 1024).decode("utf-8", errors="replace")}
    except OSError as error:
        return {"success": False, "error": str(error)}
