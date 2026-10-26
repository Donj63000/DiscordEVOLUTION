"""Explicit aioresponses transport adapter for the supported aiohttp release.

Aiohttp 3.14 added the required ``stream_writer`` constructor argument. Even the
latest published aioresponses (0.7.9) still omits it. Pass a response subclass
through aioresponses' supported ``response_class`` option rather than patching
ClientResponse globally or weakening the application's security dependency pin.

Only synthetic HTTP responses receive a completed, zero-byte stream writer:
they never upload a real request. The original aiohttp response class and its
constructor stay unchanged.
All request matching, callbacks, error handling and assertions remain provided
by aioresponses itself.
"""
from __future__ import annotations

import copy
import inspect

from aiohttp import ClientResponse, StreamReader
from aioresponses import CallbackResult, aioresponses as _aioresponses

_HAS_STREAM_WRITER = "stream_writer" in inspect.signature(ClientResponse).parameters


class _CompletedMockUpload:
    """Only upload size is read when aioresponses supplies writer=None."""

    output_size = 0


class _MemoryProtocol:
    """Flow control for an already-buffered synthetic body; no socket/parser."""

    _reading_paused = False

    def pause_reading(self):
        self._reading_paused = True

    def resume_reading(self, *, resume_parser=True):
        self._reading_paused = False


class MockClientResponse(ClientResponse):
    """Real aiohttp response backed by aioresponses' in-memory body."""

    def __init__(self, *args, **kwargs):
        if _HAS_STREAM_WRITER:
            kwargs.setdefault("stream_writer", _CompletedMockUpload())
        super().__init__(*args, **kwargs)

    @property
    def content(self):
        return self._mock_content

    @content.setter
    def content(self, unused_reader):
        # aioresponses assigns an empty reader, then feeds its buffered body.
        # Its old ResponseHandler has no parser; current aiohttp legitimately
        # requires one when real socket flow control pauses. A synthetic body
        # needs a memory-only protocol, while keeping aiohttp's real StreamReader.
        self._mock_content = StreamReader(_MemoryProtocol(), limit=2**16, loop=self._loop)


class aioresponses(_aioresponses):
    """Use the compatibility response exclusively for this test transport."""

    def add(self, *args, **kwargs):
        response_class = kwargs.get("response_class") or MockClientResponse
        kwargs["response_class"] = response_class
        callback = kwargs.get("callback")
        if callable(callback):
            async def wrapped_callback(*callback_args, **callback_kwargs):
                result = callback(*callback_args, **callback_kwargs)
                if inspect.isawaitable(result):
                    result = await result
                # CallbackResult otherwise drops the registered response_class.
                if result is not None and result.response_class is None:
                    result = copy.copy(result)
                    result.response_class = response_class
                return result
            kwargs["callback"] = wrapped_callback
        return super().add(*args, **kwargs)


__all__ = ["CallbackResult", "MockClientResponse", "aioresponses"]
