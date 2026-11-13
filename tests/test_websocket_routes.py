"""The isolation sweep's websocket list must name every websocket route the v1 API serves."""

from collections.abc import Iterator

from fastapi.routing import APIWebSocketRoute

from spoolman.api.v1.router import app
from tests_integration.tests.websocket_routes import WEBSOCKETS


def _websocket_paths(routes: list) -> Iterator[str]:
    for route in routes:
        if isinstance(route, APIWebSocketRoute):
            yield route.path
        elif hasattr(route, "original_router"):  # an include_router() entry
            yield from _websocket_paths(route.original_router.routes)


def test_sweep_lists_every_websocket_route():
    assert sorted(path for path, _ in WEBSOCKETS) == sorted(_websocket_paths(app.router.routes))
