from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


class LiveRef:
    """Proxy a name in main.py so test/runtime replacements stay visible to routes."""

    def __init__(self, namespace: MutableMapping[str, Any], name: str) -> None:
        object.__setattr__(self, "_namespace", namespace)
        object.__setattr__(self, "_name", name)

    def _value(self) -> Any:
        return self._namespace[self._name]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._value(), name)

    def __call__(self, *args, **kwargs):
        return self._value()(*args, **kwargs)

    def __getitem__(self, key):
        return self._value()[key]

    def __setitem__(self, key, value) -> None:
        self._value()[key] = value

    def __delitem__(self, key) -> None:
        del self._value()[key]

    def __iter__(self):
        return iter(self._value())

    def __len__(self) -> int:
        return len(self._value())

    def __bool__(self) -> bool:
        return bool(self._value())

    def __contains__(self, item) -> bool:
        return item in self._value()

    def __enter__(self):
        return self._value().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        return self._value().__exit__(exc_type, exc_value, traceback)

    def __str__(self) -> str:
        return str(self._value())

    def __repr__(self) -> str:
        return repr(self._value())

    def __eq__(self, other) -> bool:
        if isinstance(other, LiveRef):
            other = other._value()
        return self._value() == other

    def __ne__(self, other) -> bool:
        return not self == other

    def __fspath__(self) -> str:
        return self._value().__fspath__()

    def __truediv__(self, other):
        return self._value() / other

    def __rtruediv__(self, other):
        return other / self._value()


class RouteContext:
    """Live view of application services/helpers used while routers are assembled.

    W1.5 keeps construction in main.py while route behavior moves into domain modules.
    Framework/type symbols are bound directly for FastAPI signature evaluation, while
    runtime services/helpers use LiveRef so tests and runtime swaps made on main stay
    visible after router registration.
    """

    def __init__(self, namespace: MutableMapping[str, Any]) -> None:
        self._namespace = namespace

    def get(self, name: str) -> Any:
        return self._namespace.get(name)

    def live(self, name: str) -> LiveRef:
        return LiveRef(self._namespace, name)

    def set(self, name: str, value: Any) -> None:
        """Replace one live service while preserving existing LiveRef consumers."""
        self._namespace[name] = value
