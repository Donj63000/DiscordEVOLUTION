# Agent Instructions
This repository hosts the Discord bot for the EVOLUTION guild. The bot is deployed on a free `render.com` micro instance and kept alive via a small Flask server (`alive.py`) that is pinged by **UptimeRobot**. Because render.com's free tier provides only ephemeral storage, all data not persisted directly on Discord (such as the JSON files sent to the `#console` channel) is lost every time the bot restarts. Agents working on this project should keep this limitation in mind and avoid relying on local persistence.

- The `#console` Discord channel acts as the bot's persistent datastore. Use it to read and write any state that must survive restarts, and avoid assuming the local filesystem will retain data between deployments.
- API keys for Gemini and OpenAI are provided through Render environment variables. Never hard-code these secrets in the repository; always rely on the environment configuration at runtime.
