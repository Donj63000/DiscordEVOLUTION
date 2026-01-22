#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
from threading import Thread
from wsgiref.simple_server import make_server

from flask import Flask


def create_app():
    app = Flask(__name__)

    @app.get("/")
    def home():
        return "Le bot est en ligne ! (keep-alive)"

    return app


app = create_app()


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
    if mode == "gunicorn":
        proc = subprocess.Popen(build_gunicorn_command(port))
        if blocking:
            proc.wait()
        return proc
    if mode == "wsgiref":
        httpd = make_server("0.0.0.0", port, app)
        httpd.serve_forever()
        return None
    raise RuntimeError(f"Mode serveur invalide: {mode}")


def keep_alive():
    if os.getenv("ALIVE_IN_PROCESS", "1") != "1":
        return
    server_thread = Thread(target=run_server, kwargs={"blocking": False}, daemon=True)
    server_thread.start()


if __name__ == "__main__":
    run_server()
