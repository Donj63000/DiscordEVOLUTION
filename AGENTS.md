# Repository Guidelines

## Project Structure & Module Organization
- `main.py` bootstraps the Discord client, loads the domain modules (`activite.py`, `job.py`, `organisation.py`, `iastaff.py`, `stats.py`, etc.) and registers helpers from `utils/`.
- Command-specific workflows live in `cogs/` (`cogs/profil.py`, `cogs/annonce_ai.py`, …). Shared persistence, console helpers, parsing, and date utilities sit under `utils/`.
- Mirror modules inside `tests/` when adding coverage; reuse fixtures from `tests/test_main_evo_bot.py`. Sanitized JSON examples belong in `examples/`; `data/` contains runtime caches only and must never be committed.
- Treat `#console` as the authoritative datastore. Whenever you modify structured data (activities, jobs, recruitment, stats, etc.), persist it via `utils.console_store` or the helpers provided by each cog so the bot survives Render restarts.
- The legacy `cogs/organisation.py` is a stub raising `ImportError`; always import the root `organisation` module instead and write production-ready code (no inline comments, use expressive naming, docstrings, and tests).

### IA-assisted workflows
- `organisation.py` powers `!organisation`, orchestrating a guided Q&A with OpenAI (Responses or Chat Completions). Behaviour is controlled via `ORGANISATION_*` env vars (`ORGANISATION_BACKEND`, `ORGANISATION_TIMEOUT`, `ORGANISATION_PLANNER_TEMP`, etc.). Always resolve model aliases through `utils/openai_config.resolve_staff_model`.
- `event_conversation.py` remains the authority for `!event`. IA Staff (see below) can now collect a brief (titre/date/description) before invoking `!event`, but the DM workflow, EventStore persistence, and embed publication still live in this module.
- `iastaff.py` exposes the Staff-only `!iastaff` command (`reset`, `info`, `model …`, plus le `morning_greeting`). Lorsque `IASTAFF_ENABLE_TOOLS=1`, GPT‑5 peut appeler un catalogue d’outils qui couvrent les commandes critiques (activités, annonces, tickets, organisation, event, clear console, warnings/resetwarnings, recrutement, membre del, stats on/off/reset, job liste/joueur/métier, gestion des rôles, etc.). L’assistant sait également modifier les données persistées (ajout/suppression de métier pour un joueur, ajout/suppression de mule) en écrivant via `JobCog` et `PlayersCog` tout en publiant les snapshots dans `#console`. En dernier recours, l’outil générique `run_bot_command` permet d’appeler n’importe quelle commande avec des arguments positionnels/nommés; GPT est encouragé à chaîner plusieurs outils (questions, préparation, exécution) pour accomplir des tâches multi-étapes.
- Both IA workflows require `OPENAI_API_KEY`. Tests short-circuit gracefully when the key is missing; production deployments must provide it along with any optional `OPENAI_*` overrides.

## Build, Test, and Development Commands
Run `pip install -r requirements.txt` in your virtualenv to sync dependencies. Use `python main.py` to launch the bot locally and `gunicorn alive:app --bind 0.0.0.0:$PORT` for the keep-alive endpoint (or `python main.py` with `ALIVE_IN_PROCESS=1`). Execute `python -m pytest` (optionally `-k <pattern>`) after **every** change; do not merge or hand off work unless the entire suite passes locally.

## Coding Style & Naming Conventions
Follow PEP 8 defaults: 4-space indents, `snake_case` for functions, `UPPER_CASE` for Discord channel constants, and `PascalCase` for classes. Prefer f-strings and keep line length near 100 characters. Preserve existing type hints and co-locate command text with its handler. Stick to ASCII unless a file already uses Unicode. Ship “clean room” production code: no inline comments or `TODO` notes—use expressive names, docstrings, and tests instead.

### Logging
- Favor structured, debuggable logs via the standard `logging` module at `DEBUG` level for workflows that touch external services (OpenAI, Defender, storage). Every new control path should emit at least one actionable debug log so incidents can be traced without extra instrumentation.

## Testing Guidelines
- `python -m pytest` must pass before you share a branch. Add targeted tests for every behavioural change (examples live under `tests/` for most modules, including `tests/test_iastaff_command.py`, `tests/test_iastaff_tools.py`, `tests/test_job_command.py`, etc.).
- Use `pytest-asyncio` helpers for async code. When asserting `#console` writes or persistence, rely on the stubs/helpers already present in the test suite rather than touching real channels.
- Tests must be idempotent and not require Discord connectivity (mock network, use in-memory structures, or stub API clients such as `AsyncOpenAI` when practical).

## Commit & Pull Request Guidelines
Write short, imperative commit summaries (e.g., `Add EvoBot singleton behavior tests`) under 70 characters. PRs should describe the change, list validation steps (`python -m pytest`, manual bot checks), and attach screenshots or Discord message links for embed or announcement updates.

## Security & Persistence Notes
- Never commit tokens, locally generated JSON, or runtime caches. Render injects `DISCORD_TOKEN`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, and `FERNET_KEY`. Keep `.env.example` in sync with new secrets or feature flags (e.g., `IASTAFF_ENABLE_TOOLS`), but leave actual secrets out of the repo.
- All real data must live in the pinned `#console` messages. Modules like `activite.py`, `job.py`, `players.py`, and `stats.py` already expose helpers to dump/load their JSON via the console; do not invent ad-hoc files.
- If you set `OPENAI_ORG_ID`, only force the header by also setting `OPENAI_FORCE_ORG=1`—otherwise the SDK may inject a mismatched org. Default installs should leave these unset.
- When modifying Defender or any module that writes embeds from DM contexts, handle `Unknown message` errors by falling back to channel sends to avoid losing alerts. Emit debuggable logs around persistence, secret-handling, and external-service calls so incidents can be triaged without reproducing production data.
