import re
import urllib.request


def run(url: str) -> dict:
    if not url.startswith(("https://", "http://")):
        return {"success": False, "error": "Only HTTP(S) URLs are allowed."}
    request = urllib.request.Request(url, headers={"User-Agent": "Hsx/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read(512 * 1024).decode("utf-8", errors="replace")
        text = re.sub(
            r"<script.*?</script>|<style.*?</style>|<[^>]+>",
            " ",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return {"success": True, "text": " ".join(text.split())[:100_000]}
    except (OSError, UnicodeError) as error:
        return {"success": False, "error": str(error)}
