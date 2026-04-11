from __future__ import annotations

import importlib
import inspect
from functools import lru_cache
from typing import Any, Awaitable, Callable, Protocol

import httpx

from agent_shield.config.schema import AgentConfig


class CustomRequestFn(Protocol):
    """Signature for a user-supplied request function.

    The function must be async. It receives the httpx client, the agent
    config, and the already-templated body, and must return an
    `httpx.Response`. Custom request functions are responsible for any
    auth signing, multipart packaging, or transport-level concerns.
    """

    def __call__(
        self,
        client: httpx.AsyncClient,
        agent_config: AgentConfig,
        body: Any,
    ) -> Awaitable[httpx.Response]:
        ...


class CustomExtractFn(Protocol):
    """Signature for a user-supplied response extraction function.

    Pure (synchronous) function that takes an `httpx.Response` and
    returns the assistant's response text. Use this when the response
    format is too complex for jmespath alone (XML, custom envelopes,
    multipart, etc.).
    """

    def __call__(self, response: httpx.Response) -> str:
        ...


@lru_cache(maxsize=128)
def resolve_hook(dotted_path: str) -> Callable:
    """Import and return a callable from a dotted path like 'module.func'.

    Cached so each unique path is only imported once. Raises ValueError
    with a clear message on import or attribute errors.
    """
    if "." not in dotted_path:
        raise ValueError(
            f"Invalid hook path '{dotted_path}': must be 'module.function'"
        )

    module_path, _, attr_name = dotted_path.rpartition(".")

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValueError(
            f"Cannot import module '{module_path}' for hook '{dotted_path}': {e}"
        ) from e

    try:
        fn = getattr(module, attr_name)
    except AttributeError as e:
        raise ValueError(
            f"Module '{module_path}' has no attribute '{attr_name}' "
            f"(referenced as hook '{dotted_path}')"
        ) from e

    if not callable(fn):
        raise ValueError(f"Hook '{dotted_path}' is not callable")

    return fn


def resolve_request_hook(path: str) -> CustomRequestFn:
    """Resolve a custom request hook and verify it's an async function."""
    fn = resolve_hook(path)
    if not inspect.iscoroutinefunction(fn):
        raise ValueError(
            f"Custom request hook '{path}' must be an async function"
        )
    return fn  # type: ignore[return-value]


def resolve_extract_hook(path: str) -> CustomExtractFn:
    """Resolve a custom extract hook (sync callable)."""
    return resolve_hook(path)  # type: ignore[return-value]
