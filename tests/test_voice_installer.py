import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).parents[1] / "scripts" / "setup_voice_model.py"
    spec = importlib.util.spec_from_file_location("hsx_voice_installer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_vosk_installer_verifies_archive_checksum(tmp_path: Path):
    module = _module()
    archive = tmp_path / "model.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(f"{module.MODEL_NAME}/conf/model.conf", "model")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    destination = tmp_path / "models" / module.MODEL_NAME

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        module.install_model(destination, "0" * 64, archive.as_uri())
    assert not destination.exists()

    assert module.install_model(destination, checksum, archive.as_uri()) == destination.resolve()
    assert (destination / "conf" / "model.conf").read_text(encoding="utf-8") == "model"
