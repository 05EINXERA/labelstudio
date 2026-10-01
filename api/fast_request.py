"""A route class whose request bodies are parsed with `fastjson`.

FastAPI parses a JSON body with `request.json()` -- the stdlib parser, run on
the **event loop**. A save's body is a few MB whose bulk is one long string (the
annotation set, as JSON inside JSON), and parsing it was 6% of all GIL time on
the live server, all of it stalling every other request while it ran
(.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md). `fastjson.loads`
parses the same document ~3x faster and falls back to the stdlib for anything
`orjson` refuses, so behaviour -- including the 422 for malformed JSON -- is
unchanged.

Applied to the tasks router only, because that is where the large bodies are.
This is FastAPI's documented "custom Request and APIRoute class" pattern.
"""
from typing import Any, Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute

import fastjson


class FastJSONRequest(Request):
    async def json(self) -> Any:
        if not hasattr(self, "_json"):
            body = await self.body()
            self._json = fastjson.loads(body)
        return self._json


class FastJSONRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            return await original(FastJSONRequest(request.scope, request.receive))

        return handler
