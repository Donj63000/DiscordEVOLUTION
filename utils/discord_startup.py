"""Je laisse passer les limitations Discord sans redémarrer le processus."""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import random
import signal

import discord

log = logging.getLogger(__name__)


def retry_delay(attempt, headers):
    """Je respecte le délai serveur lorsqu'il dépasse mon attente progressive."""
    delay = (900, 1800, 3600)[min(attempt - 1, 2)]
    raw = headers.get("Retry-After", "")
    try:
        server_delay = float(raw)
    except (ValueError, TypeError):
        try:
            deadline = parsedate_to_datetime(raw)
            server_delay = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            server_delay = 0
    if math.isfinite(server_delay) and server_delay > delay:
        delay = server_delay
    return delay + random.uniform(1, 10)


async def run_clients(factory, initial=None):
    """Je recrée uniquement les clients limités avant le chargement des extensions."""
    attempt = 0
    client = initial
    while True:
        if client is None:
            client = factory()
        attempt += 1
        delay = None
        log.debug("Discord startup attempt=%s", attempt)
        async with client:
            try:
                await client.start(client.token, reconnect=True)
            except discord.HTTPException as exc:
                if exc.status != 429 or client._startup_setup_started:
                    raise
                delay = retry_delay(attempt, exc.response.headers)
                log.warning(
                    "Discord startup limited attempt=%s status=429 retry_in_seconds=%.1f",
                    attempt, delay,
                )
        if delay is None:
            return
        client = None
        await asyncio.sleep(delay)


async def run_with_shutdown(factory, initial=None):
    """Je ferme le client aussi lorsque Render envoie SIGTERM."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    registered = False
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            registered = True
        except NotImplementedError:
            pass
        await run_clients(factory, initial)
    finally:
        if registered:
            loop.remove_signal_handler(signal.SIGTERM)
