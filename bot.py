"""Compatibility proxy for the verification bot."""

from __future__ import annotations

import sys
from types import ModuleType

from bots import verification as _impl


class _ModuleProxy(ModuleType):
    """Proxy module that forwards attribute access to the implementation."""

    def __getattr__(self, name: str):  # type: ignore[override]
        return getattr(_impl, name)

    def __setattr__(self, name: str, value) -> None:  # type: ignore[override]
        setattr(_impl, name, value)
        super().__setattr__(name, getattr(_impl, name))

    def __delattr__(self, name: str) -> None:  # type: ignore[override]
        delattr(_impl, name)
        super().__delattr__(name)

    def __dir__(self):
        return sorted(set(dir(_impl)) | set(super().__dir__()))


proxy = _ModuleProxy(__name__)
proxy.__dict__.update(_impl.__dict__)
proxy.__doc__ = __doc__

sys.modules[__name__] = proxy
