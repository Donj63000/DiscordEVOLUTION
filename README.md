# DiscordEVOLUTION

This repository contains a Discord bot used on the EVOLUTION server.
It provides many features such as statistics, ticket management and an
interface with Google Gemini.

## Job Module

The `job.py` cog lets players record their in‑game professions.
Recent improvements include:

- Migration of old entries stored under player nicknames to Discord IDs.
- Validation of job levels (must be between 1 and 200).
- Loading of `jobs_data.json` from console attachments.
- New `!job del <job_name>` command to remove a profession.
- Job names with spaces are now handled directly with
  `!job <job_name> <niveau>` (the `add` keyword remains as an alias).

Install dependencies from `requirements.txt` before running the bot.
