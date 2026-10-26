"""Bounded local HTTP exchanges, including a real Gunicorn process."""

import gzip
import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from aiohttp.test_utils import TestClient, TestServer
import pytest

import alive


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [gzip.compress(b"a" * 8192), b"not a gzip stream"])
async def test_encoded_unread_body_is_rejected_before_parser_decompression(body):
    alive.set_ready(True)
    try:
        async with TestClient(TestServer(alive.create_aiohttp_app())) as client:
            response = await client.get("/", data=body, headers={"Content-Encoding": "gzip"})
            assert response.status == 415
            await response.read()
            ready = await client.get("/ready")
            assert ready.status == 200
            assert await ready.text() == "ready"
    finally:
        alive.set_ready(False)


@pytest.mark.asyncio
async def test_large_unread_body_is_rejected_and_normal_routes_survive():
    async with TestClient(TestServer(alive.create_aiohttp_app())) as client:
        response = await client.get("/", data=b"a" * 8192)
        assert response.status == 413
        await response.read()
        for method, path, status in [("GET", "/", 200), ("HEAD", "/", 200), ("GET", "/missing", 404), ("POST", "/", 405)]:
            response = await client.request(method, path)
            assert response.status == status
            await response.read()


def test_wsgi_rejects_encoded_and_oversized_bodies():
    client = alive.create_app().test_client()
    assert client.get("/", data=b"not gzip", headers={"Content-Encoding": "gzip"}).status_code == 415
    assert client.get("/", data=b"a" * 8192).status_code == 413
    assert client.get("/").status_code == 200


@pytest.mark.skipif(os.name != "posix", reason="Gunicorn requires a POSIX platform")
def test_real_gunicorn_health_server_starts_and_stops():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "alive:app", "--bind", f"fd://{listener.fileno()}", "--workers", "1", "--threads", "1"],
            cwd=root,
            pass_fds=(listener.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "Gunicorn exited before serving health checks"
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
                try:
                    connection.request("GET", "/")
                    response = connection.getresponse()
                    assert response.status == 200
                    assert response.read().decode() == alive.HOME_TEXT
                    break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
                finally:
                    connection.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.returncode == 0
