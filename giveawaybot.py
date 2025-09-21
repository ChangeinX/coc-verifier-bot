"""Compatibility proxy for the giveaway bot."""

from __future__ import annotations

import sys
from types import ModuleType

from bots import giveaway as _impl


class _ModuleProxy(ModuleType):
    def __getattr__(self, name):  # type: ignore[override]
        return getattr(_impl, name)

    def __setattr__(self, name, value):  # type: ignore[override]
        setattr(_impl, name, value)
        super().__setattr__(name, getattr(_impl, name))

    def __delattr__(self, name):  # type: ignore[override]
        delattr(_impl, name)
        super().__delattr__(name)

    def __dir__(self):
        return sorted(set(dir(_impl)) | set(super().__dir__()))


def _install_proxy() -> None:
    proxy = _ModuleProxy(__name__)
    proxy.__dict__.update(_impl.__dict__)
    proxy.__doc__ = __doc__
    sys.modules[__name__] = proxy


_install_proxy()
