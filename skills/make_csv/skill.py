import csv
from pathlib import Path

from config import OUTPUTS_DIR


def run(filename: str, headers: list, rows: list) -> dict:
    path = OUTPUTS_DIR / f"{Path(filename).stem}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)
    return {"success": True, "output_path": str(path)}