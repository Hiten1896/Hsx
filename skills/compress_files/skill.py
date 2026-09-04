from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from config import OUTPUTS_DIR


def run(filenames: list, zip_name: str) -> dict:
    archive = OUTPUTS_DIR / f"{Path(zip_name).stem}.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for filename in filenames:
            source = OUTPUTS_DIR / Path(filename).name
            if source.exists():
                zip_file.write(source, source.name)
    return {"success": True, "output_path": str(archive)}