# Repository Guidelines

## Project Structure & Module Organization
`main.py` bootstraps the Discord client, loading domain modules such as `activite.py`, `job.py`, and `stats.py`, then registers helpers from `utils/`. Command-specific workflows live in `cogs/` (for example `cogs/profil.py` and `cogs/annonce_ai.py`). Shared persistence, console helpers, and date utilities sit under `utils/`. Mirror modules inside `tests/` when adding coverage; reuse fixtures from `tests/test_main_evo_bot.py`. Keep sanitized JSON examples in `examples/`, and remember `data/` is runtime-only cache that must never be committed. Treat `#console` as the authoritative datastore; persist JSON there via `utils.console_store` so the bot survives Render restarts.

## Build, Test, and Development Commands
Run `pip install -r requirements.txt` in your virtualenv to sync dependencies. Use `python main.py` to launch the bot locally and `python alive.py` for the keep-alive Flask endpoint. Execute `python -m pytest` (optionally `-k <pattern>`) to run the async-aware test suite before every PR.

## Coding Style & Naming Conventions
Follow PEP 8 defaults: 4-space indents, `snake_case` for functions, `UPPER_CASE` for Discord channel constants, and `PascalCase` for classes. Prefer f-strings and keep line length near 100 characters. Preserve existing type hints and co-locate command text with its handler. Stick to ASCII unless a file already uses Unicode.

## Testing Guidelines
Pytest with `pytest-asyncio` powers async tests; leverage `ConsoleStore` stubs or `aioresponses` when asserting `#console` writes. Name tests `test_<feature>_<behavior>` and ensure they are idempotent so CI can run without Discord access.

## Commit & Pull Request Guidelines
Write short, imperative commit summaries (e.g., `Add EvoBot singleton behavior tests`) under 70 characters. PRs should describe the change, list validation steps (`python -m pytest`, manual bot checks), and attach screenshots or Discord message links for embed or announcement updates.

## Security & Persistence Notes
Never commit tokens or generated JSON. Render injects `DISCORD_TOKEN`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, and `FERNET_KEY` at runtime. Review `.env.example` after adding secrets, and always persist real JSON payloads in the pinned `#console` messages rather than the local filesystem.
