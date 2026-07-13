import json
from pathlib import Path

from sloppy_checker.main import app


def main() -> None:
    target = Path(__file__).resolve().parents[3] / "openapi.json"
    target.write_text(json.dumps(app.openapi(), indent=2) + "\n")


if __name__ == "__main__":
    main()

