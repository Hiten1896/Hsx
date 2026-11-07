import base64
import io

from PIL import Image

from agent import llm_client
from agent.llm.router import LLMRouter
from agent.llm.types import LLMEvent
from agent.workspace import Workspace
from skills.describe_image import skill as describe_image


def test_describe_image_sends_resized_jpeg_part_to_fake_gemini(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspace"
    work_dir = workspace_root / "Hsx Work"
    work_dir.mkdir(parents=True)
    image_path = workspace_root / "Uploads" / "large.png"
    image_path.parent.mkdir()
    Image.new("RGB", (2000, 1700), "blue").save(image_path)
    monkeypatch.setattr(describe_image, "WORKSPACE", Workspace(workspace_root, work_dir))
    captured = {}

    class FakeGemini:
        name = "gemini"
        model = "fake-vision"

        def generate(self, request):
            image_part = next(
                block for message in request.messages
                for block in message["content"]
                if block.get("type") == "image"
            )
            data = base64.b64decode(image_part["data"], validate=True)
            assert image_part["mime_type"] == "image/jpeg"
            assert len(data) <= 2 * 1024 * 1024
            with Image.open(io.BytesIO(data)) as decoded:
                assert max(decoded.size) <= 1568
            captured["request"] = request
            yield LLMEvent(type="text_delta", text="A blue image.")
            yield LLMEvent(type="stop")

    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([FakeGemini()]))
    monkeypatch.setattr(
        llm_client,
        "PROVIDER_BANDIT",
        llm_client.ProviderBandit(tmp_path / "bandit.json"),
    )

    result = describe_image.run("Uploads/large.png", "What color is it?")

    assert result["success"] is True
    assert result["path"] == "Uploads/large.png"
    assert result["description"] == "A blue image."
    assert captured["request"].messages[0]["content"][0]["text"] == "What color is it?"


def test_image_input_is_only_allowed_when_named_or_attached():
    from agent.orchestrator import _image_path_was_named

    assert _image_path_was_named(
        "Describe the attached file.\nUploaded workspace files:\n- Hsx Work/Uploads/photo.png",
        "Hsx Work/Uploads/photo.png",
    )
    assert _image_path_was_named("Please inspect photo.png.", "photo.png")
    assert not _image_path_was_named("Describe the screenshot.", "private/secret.png")
    assert not _image_path_was_named("Please inspect photo.png.", "private/photo.png")
    assert not _image_path_was_named("Please inspect photo.png.", "photo.png.bak")
