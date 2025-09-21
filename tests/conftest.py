from __future__ import annotations

import sys
import types

if "coc" not in sys.modules:
    coc_module = types.ModuleType("coc")

    class DummyHTTPException(Exception):
        def __init__(
            self, status: int | None = None, *args, **kwargs
        ):  # pragma: no cover - simple stub
            super().__init__(*args)
            self.status = status

    class DummyNotFound(DummyHTTPException):
        pass

    class DummyClient:
        async def login(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            return None

        async def close(self):  # pragma: no cover - simple stub
            return None

        async def get_player(self, *_args, **_kwargs):  # pragma: no cover - simple stub
            return None

    class DummyPlayer:
        def __init__(self, **kwargs):  # pragma: no cover - simple stub
            self.__dict__.update(kwargs)

    coc_module.Client = DummyClient
    coc_module.Player = DummyPlayer
    coc_module.HTTPException = DummyHTTPException
    coc_module.NotFound = DummyNotFound
    sys.modules["coc"] = coc_module
