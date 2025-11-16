# Repository Guidelines

## Project Structure & Module Organization
`main.py` bootstraps the Discord client, loading domain modules such as `activite.py`, `job.py`, `organisation.py`, `iastaff.py`, and `stats.py`, then registers helpers from `utils/`. Command-specific workflows live in `cogs/` (for example `cogs/profil.py` and `cogs/annonce_ai.py`). Shared persistence, console helpers, and date utilities sit under `utils/`. Mirror modules inside `tests/` when adding coverage; reuse fixtures from `tests/test_main_evo_bot.py`. Keep sanitized JSON examples in `examples/`, and remember `data/` is runtime-only cache that must never be committed. Treat `#console` as the authoritative datastore; persist JSON there via `utils.console_store` so the bot survives Render restarts. The legacy `cogs/organisation.py` is now a stub raising `ImportError`; always import the root `organisation` module and write production-ready code (no inline comments, rely on clean naming/tests for documentation).

### IA-assisted workflows
- `organisation.py` powers the `!organisation` command. It relies on OpenAI (Responses or Chat Completions) to run a guided Q&A and generate an announcement embed. Tune behaviour via `ORGANISATION_*` env vars (`ORGANISATION_BACKEND`, `ORGANISATION_TIMEOUT`, `ORGANISATION_PLANNER_TEMP`, etc.). Use `utils/openai_config.resolve_staff_model` to keep model aliases consistent.
- `event_conversation.py` stays responsible for `!event` (DM flow + EventStore), while `iastaff.py` exposes `!iastaff` for Staff-only inline assistance (`reset`, `info`, `model …` subcommands, plus the scheduled `morning_greeting` task). When testing, ensure `OPENAI_API_KEY` is present; otherwise both modules short-circuit with a friendly error.

## Build, Test, and Development Commands
Run `pip install -r requirements.txt` in your virtualenv to sync dependencies. Use `python main.py` to launch the bot locally and `python alive.py` for the keep-alive Flask endpoint. Execute `python -m pytest` (optionally `-k <pattern>`) after **every** change; do not merge or hand off work unless the entire suite passes locally.

## Coding Style & Naming Conventions
Follow PEP 8 defaults: 4-space indents, `snake_case` for functions, `UPPER_CASE` for Discord channel constants, and `PascalCase` for classes. Prefer f-strings and keep line length near 100 characters. Preserve existing type hints and co-locate command text with its handler. Stick to ASCII unless a file already uses Unicode. Ship “clean room” production code: no inline comments or `TODO` notes—use expressive names, docstrings, and tests instead.

### Logging
- Favor structured, debuggable logs via the standard `logging` module at `DEBUG` level for workflows that touch external services (OpenAI, Defender, storage). Every new control path should emit at least one actionable debug log so incidents can be traced without extra instrumentation.

## Testing Guidelines
Pytest with `pytest-asyncio` powers async tests; leverage `ConsoleStore` stubs or `aioresponses` when asserting `#console` writes. Name tests `test_<feature>_<behavior>` and ensure they are idempotent so CI can run without Discord access. Every modification must be accompanied by targeted unit tests plus a full-suite run (`python -m pytest`) before submitting or sharing the branch.

## Commit & Pull Request Guidelines
Write short, imperative commit summaries (e.g., `Add EvoBot singleton behavior tests`) under 70 characters. PRs should describe the change, list validation steps (`python -m pytest`, manual bot checks), and attach screenshots or Discord message links for embed or announcement updates.

## Security & Persistence Notes
Never commit tokens or generated JSON. Render injects `DISCORD_TOKEN`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, and `FERNET_KEY` at runtime. Review `.env.example` after adding secrets, and always persist real JSON payloads in the pinned `#console` messages rather than the local filesystem. If you set `OPENAI_ORG_ID`, only force the header by also setting `OPENAI_FORCE_ORG=1` (otherwise the SDK will auto-inject a mismatched org); default installs should leave this unset. When modifying Defender or any module that writes embeds from DM contexts, handle `Unknown message` errors by falling back to channel sends to avoid dropping alerts. Always emit debug logs around persistence or secret-handling code paths so incidents can be triaged without reproducing production data.
