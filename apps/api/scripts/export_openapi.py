import json
from pathlib import Path

from sourcetrace.main import app


def main() -> None:
    target = Path(__file__).resolve().parents[2] / "web" / "openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI schema written to {target}")


if __name__ == "__main__":
    main()
