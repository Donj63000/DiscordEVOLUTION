#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from threading import Thread
from wsgiref.simple_server import make_server

HOME_TEXT = "Le bot est en ligne ! (keep-alive)"
_wsgi_app = None


def create_app():
    # Flask reste disponible pour les modes historiques, sans être chargé
    # pour le simple serveur aiohttp.
    from flask import Flask

    app = Flask(__name__)

    @app.get("/")
    def home():
        return HOME_TEXT

    return app


def get_wsgi_app():
    global _wsgi_app
    if _wsgi_app is None:
        _wsgi_app = create_app()
    return _wsgi_app


def __getattr__(name):
    # Préserve notamment la commande existante : gunicorn alive:app.
    if name == "app":
        return get_wsgi_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_aiohttp_app():
    from aiohttp import web

    app = web.Application(client_max_size=4096)

    async def home(request):
        return web.Response(text=HOME_TEXT)

    app.router.add_get("/", home)
    return app


def resolve_port() -> int:
    value = os.getenv("PORT", "8080")
    try:
        return int(value)
    except ValueError:
        return 8080


def resolve_server_mode() -> str:
    mode = os.getenv("ALIVE_SERVER")
    if mode:
        return mode.strip().lower()
    if os.name == "nt":
        return "wsgiref"
    return "gunicorn"


def build_gunicorn_command(port: int) -> list[str]:
    workers = os.getenv("ALIVE_WORKERS", "1")
    threads = os.getenv("ALIVE_THREADS", "4")
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "alive:app",
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        str(workers),
        "--threads",
        str(threads),
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
    ]


def run_server(blocking: bool = True):
    port = resolve_port()
    mode = resolve_server_mode()
    if mode == "aiohttp":
        from aiohttp import web

        # keep_alive() nous exécute dans un thread : les signaux appartiennent
        # au thread principal du bot. Aucun processus Gunicorn n'est lancé.
        web.run_app(
            create_aiohttp_app(),
            host="0.0.0.0",
            port=port,
            handle_signals=False,
            print=None,
            access_log=None,
        )
        return None
    if mode == "gunicorn":
        proc = subprocess.Popen(build_gunicorn_command(port))
        if blocking:
            proc.wait()
        return proc
    if mode == "wsgiref":
        httpd = make_server("0.0.0.0", port, get_wsgi_app())
        httpd.serve_forever()
        return None
    raise RuntimeError(f"Mode serveur invalide: {mode}")


def keep_alive():
    if os.getenv("ALIVE_IN_PROCESS", "1") != "1":
        return
    server_thread = Thread(
        target=run_server, kwargs={"blocking": False},
        name="http-health", daemon=True,
    )
    server_thread.start()


if __name__ == "__main__":
    run_server()
