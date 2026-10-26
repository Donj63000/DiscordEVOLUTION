"""The test transport supports aiohttp without altering the production SDK."""
from __future__ import annotations

import asyncio
import inspect

import aiohttp
from aiohttp.client_reqrep import ClientResponse
import pytest

from tests_support.http_mock import CallbackResult, MockClientResponse, aioresponses


@pytest.mark.asyncio
async def test_transport_keeps_original_response_and_session_methods_unchanged():
    original_init = ClientResponse.__init__
    original_request = aiohttp.ClientSession._request
    original_signature = inspect.signature(ClientResponse)
    async with aiohttp.ClientSession() as client:
        with aioresponses() as transport:
            transport.get("https://transport.invalid/body", body=b"synthetic", status=206,
                          headers={"X-Synthetic": "yes"})
            async with client.get("https://transport.invalid/body") as response:
                assert isinstance(response, ClientResponse)
                assert isinstance(response, MockClientResponse)
                assert response.status == 206
                assert response.headers["X-Synthetic"] == "yes"
                assert await response.read() == b"synthetic"
    assert aiohttp.ClientResponse is ClientResponse
    assert ClientResponse.__init__ is original_init
    assert inspect.signature(ClientResponse) == original_signature
    assert aiohttp.ClientSession._request is original_request


@pytest.mark.asyncio
async def test_transport_preserves_json_and_http_errors():
    async with aiohttp.ClientSession() as client:
        with aioresponses() as transport:
            transport.get("https://transport.invalid/json", payload={"count": 2})
            transport.get("https://transport.invalid/missing", status=404, body="missing")
            async with client.get("https://transport.invalid/json") as response:
                assert await response.json() == {"count": 2}
            async with client.get("https://transport.invalid/missing") as response:
                with pytest.raises(aiohttp.ClientResponseError) as raised:
                    response.raise_for_status()
                assert raised.value.status == 404
                assert await response.text() == "missing"


@pytest.mark.asyncio
async def test_transport_preserves_callbacks_timeouts_and_request_assertions():
    received = []

    async def callback(url, **kwargs):
        received.append((str(url), kwargs["headers"]["X-Test"]))
        return CallbackResult(status=201, payload={"created": True})

    async with aiohttp.ClientSession() as client:
        with aioresponses() as transport:
            transport.post("https://transport.invalid/callback", callback=callback)
            transport.get("https://transport.invalid/timeout", exception=asyncio.TimeoutError())
            async with client.post("https://transport.invalid/callback", headers={"X-Test": "fixture"}) as response:
                assert response.status == 201
                assert await response.json() == {"created": True}
            with pytest.raises(asyncio.TimeoutError):
                await client.get("https://transport.invalid/timeout")
            transport.assert_called_with("https://transport.invalid/timeout", allow_redirects=True)
    assert received == [("https://transport.invalid/callback", "fixture")]


@pytest.mark.asyncio
async def test_transport_respects_an_explicit_custom_response_class():
    class CustomResponse(MockClientResponse):
        pass

    async with aiohttp.ClientSession() as client:
        with aioresponses() as transport:
            transport.get("https://transport.invalid/custom", response_class=CustomResponse,
                          body="custom")
            async with client.get("https://transport.invalid/custom") as response:
                assert type(response) is CustomResponse
                assert await response.text() == "custom"


@pytest.mark.asyncio
async def test_transport_preserves_large_buffered_bodies_and_callback_results():
    body = b"x" * (256 * 1024)
    result = CallbackResult(body=body, content_type="application/octet-stream")
    async with aiohttp.ClientSession() as client:
        with aioresponses() as transport:
            transport.get("https://transport.invalid/large", callback=lambda *a, **k: result)
            async with client.get("https://transport.invalid/large") as response:
                assert await response.read() == body
    assert result.response_class is None  # The caller's reusable result is not mutated.
