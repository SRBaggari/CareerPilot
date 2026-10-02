"""Production preflight: ``uv run python -m app.check`` (``--production`` to apply the
production rules whatever APP_ENV says).

Checks the configuration, the database (reachable, pgvector installed, migrations current),
file storage, the browser used for assisted applications, and the AI providers. Prints one
line per check and exits non-zero if anything required fails, so a deploy can gate on it.
Never prints secret values.
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.core.config import Settings, production_problems


def _line(ok: bool | None, name: str, detail: str) -> None:
    mark = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
    print(f"[{mark}] {name}: {detail}")


async def _database() -> list[tuple[bool | None, str, str]]:
    from app.db.session import check_database, dispose_engine

    status = await check_database()
    await dispose_engine()
    return [
        (status.connected, "database", "reachable" if status.connected else str(status.detail)),
        (status.pgvector, "pgvector", "installed" if status.pgvector else "not installed"),
        (
            bool(status.migrations_current),
            "migrations",
            f"at {status.migration}" if status.migrations_current else str(status.detail),
        ),
    ]


def _storage(settings: Settings) -> tuple[bool, str, str]:
    root = Path(settings.storage_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".check-"):
            pass
    except OSError as exc:
        return False, "file storage", f"{root} is not writable ({type(exc).__name__})"
    return True, "file storage", f"{root.resolve()} is writable"


def _browser() -> tuple[bool | None, str, str]:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = Path(pw.chromium.executable_path)
    except Exception as exc:  # reported, not fatal
        return None, "browser", f"Playwright unavailable ({type(exc).__name__})"
    if path.exists():
        return True, "browser", "Chromium installed (assisted applications available)"
    return None, "browser", "Chromium missing: run `playwright install --with-deps chromium`"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production", action="store_true", help="apply production rules")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError as exc:
        _line(False, "configuration", "; ".join(e["msg"] for e in exc.errors()))
        return 1
    results: list[tuple[bool | None, str, str]] = []
    problems = production_problems(settings) if args.production else []
    results.append(
        (not problems, "configuration", " ".join(problems) or f"APP_ENV={settings.app_env}")
    )
    results += asyncio.run(_database())
    results.append(_storage(settings))
    results.append(_browser())
    results.append(
        (
            True if settings.anthropic_api_key else None,
            "AI provider",
            "Anthropic key configured"
            if settings.anthropic_api_key
            else "no ANTHROPIC_API_KEY: rule-based generation only",
        )
    )
    if settings.embedding_provider == "voyage" and not settings.voyage_api_key:
        results.append((False, "embeddings", "EMBEDDING_PROVIDER=voyage needs VOYAGE_API_KEY"))
    else:
        results.append((True, "embeddings", settings.embedding_provider))

    for ok, name, detail in results:
        _line(ok, name, detail)
    failed = [name for ok, name, _ in results if ok is False]
    print("All required checks passed." if not failed else f"Failed: {', '.join(failed)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
