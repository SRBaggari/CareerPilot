"""Serve the mock application site: ``uv run python -m app.automation.mock_site``."""

import uvicorn

from app.automation.mock_site import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8790)
