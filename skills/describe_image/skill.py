import base64
import importlib
import io
from pathlib import Path

from PIL import Image

from agent.llm_client import call_llm
from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 1568


def _jpeg_bytes(path: Path) -> bytes:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("That image is larger than the 20 MB analysis limit.")
    try:
        image_context = Image.open(path)
    except (OSError, ValueError) as error:
        raise ValueError("I couldn't read that image. Try a JPEG, PNG, or HEIC file.") from error
    output = io.BytesIO()
    with image_context as image:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        quality = 88
        while True:
            output.seek(0)
            output.truncate()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= MAX_OUTPUT_BYTES:
                return output.getvalue()
            if quality <= 42:
                raise ValueError("That image could not be resized below the 2 MB provider limit.")
            quality -= 10


def run(path: str, question: str) -> dict[str, str | bool]:
    image_path = WORKSPACE.resolve_read(path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found in the workspace: {path}")
    if image_path.suffix.casefold() == ".heic":
        try:
            pillow_heif = importlib.import_module("pillow_heif")
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
    description = "\n".join(
        block.text for block in response.content
        if getattr(block, "type", "") == "text"
    ).strip()
    if not description:
        raise RuntimeError("The configured vision provider returned no image description.")
    return {
        "success": True,
        "path": image_path.relative_to(WORKSPACE.root).as_posix(),
        "description": description,
        "message": f"Described {image_path.name}.",
    }
