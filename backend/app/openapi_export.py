"""Write the OpenAPI schema to `frontend/openapi.json` for frontend type generation
(ADR-0011):

    uv run python -m app.openapi_export

The frontend's `npm run gen:api` then turns that committed file into TypeScript
types — no running server required, so it is reproducible in CI. The file is written
as UTF-8 (no BOM) directly, rather than via shell redirection, so it round-trips
cleanly on any platform.
"""

import json
from pathlib import Path

from app.main import app

_OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    schema = json.dumps(app.openapi(), indent=2, sort_keys=True)
    _OUTPUT.write_text(schema + "\n", encoding="utf-8")
    print(f"Wrote {_OUTPUT}")


if __name__ == "__main__":
    main()
