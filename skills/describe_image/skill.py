import base64
import io

from PIL import Image

from agent.llm_client import call_llm
from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 1568


def _jpeg_bytes(path):
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("That image is larger than the 20 MB analysis limit.")
    try:
        image = Image.open(path)
    except Exception as error:
        raise ValueError("I couldn't read that image. Try a JPEG, PNG, or HEIC file.") from error
    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    output = io.BytesIO()
    quality = 88
    while True:
        output.seek(0)
        output.truncate()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() <= MAX_OUTPUT_BYTES:
            return output.getvalue()
        if quality <= 42:
            raise ValueError("That image could not be resized below the 4 MB analysis limit.")
        quality -= 10


def run(path: str, question: str) -> str:
    image_path = WORKSPACE.resolve_read(path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found in the workspace: {path}")
    if image_path.suffix.casefold() == ".heic":
        try:
            import pillow_heif
        except ImportError as error:
            raise RuntimeError("HEIC support needs the approved pillow-heif package in the Hsx environment.") from error
        pillow_heif.register_heif_opener()
    encoded = base64.b64encode(_jpeg_bytes(image_path)).decode("ascii")
    prompt = question.strip() or "Describe this image."
    response = call_llm(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image", "mime_type": "image/jpeg", "data": encoded},
        ]}],
        [],
        sensitive=True,
        required_provider="gemini",
        disable_panel=True,
    )
    if response is None:
        raise RuntimeError("No configured vision provider could describe that image.")
    return "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")
