"""Transactional cleanup candidate for HTTPcore issue #1113 / HTTPX #3782.

A research-only, version-guarded in-memory candidate. No package source is
modified. The production change must be reviewed by upstream maintainers.
The two candidate variants relocate or compensate an early commit of `_closed`.
"""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
import inspect
from typing import Iterator


class UnsupportedTarget(RuntimeError):
    pass


def candidate_patch_diff() -> str:
    """Backward-compatible alias for a real context-aware candidate Diff."""
    return upstream_rearm_patch()


def upstream_rearm_patch() -> str:
    """Full-file unified diff for HTTPcore 1.0.9's retry-enabling variant.

    This is a narrowly scoped, mechanically generated review candidate. It
    must not be applied to other versions or considered safe without upstream
    concurrency, Trio and error-path testing.
    """
    from difflib import unified_diff
    from pathlib import Path
    import httpcore
    import httpcore._async.http11 as module
    if httpcore.__version__ != "1.0.9":
        raise UnsupportedTarget("Pinned HTTPcore 1.0.9 only")
    source = Path(module.__file__).read_text(encoding="utf-8")
    old = ("    async def aclose(self) -> None:\n"
           "        if not self._closed:\n"
           "            self._closed = True\n"
           "            async with Trace(\"response_closed\", logger, self._request):\n"
           "                await self._connection._response_closed()\n")
    new = ("    async def aclose(self) -> None:\n"
           "        if not self._closed:\n"
           "            self._closed = True\n"
           "            try:\n"
           "                async with Trace(\"response_closed\", logger, self._request):\n"
           "                    await self._connection._response_closed()\n"
           "            except BaseException:\n"
           "                # Cleanup may be retried by a higher-level owner.\n"
           "                self._closed = False\n"
           "                raise\n")
    if source.count(old) != 1:
        raise UnsupportedTarget("Unrecognized or nonunique source block")
    modified = source.replace(old,new)
    return "".join(unified_diff(source.splitlines(keepends=True),modified.splitlines(keepends=True),
                                fromfile="a/httpcore/_async/http11.py",
                                tofile="b/httpcore/_async/http11.py",n=3))


@contextmanager
def instrument_candidate(strategy: str) -> Iterator[None]:
    """Temporarily install an in-memory candidate on HTTPcore 1.0.9.

    Valid choices: baseline, shield-only, rearm-after-cancellation, defer-commit,
    singleflight-commit (asyncio-only experimental candidate).
    The shield-only candidate is a deliberate competing **negative** baseline.
    Restore the original even on exceptions. Do not use in concurrent callers.
    """
    import httpcore
    from httpcore._async.http11 import HTTP11ConnectionByteStream
    from httpcore._trace import Trace
    from httpcore._synchronization import AsyncShieldCancellation
    import httpcore._async.http11 as h11_module

    options = {"baseline", "shield-only", "rearm-after-cancellation", "defer-commit", "singleflight-commit"}
    if strategy not in options:
        raise ValueError(f"Unknown strategy {strategy!r}")
    if httpcore.__version__ != "1.0.9":
        raise UnsupportedTarget(f"Only inspected HTTPcore 1.0.9, got {httpcore.__version__}")
    cls = HTTP11ConnectionByteStream
    original = cls.aclose
    source = inspect.getsource(original)
    # Reject silent application to a different state-machine implementation.
    if not ("self._closed = True" in source and "await self._connection._response_closed()" in source
            and source.index("self._closed = True") < source.index("await self._connection._response_closed()")):
        raise UnsupportedTarget("Unexpected upstream close protocol: refusing to patch")

    async def shield_only(self) -> None:
        with AsyncShieldCancellation():
            await original(self)

    async def rearm_after_cancellation(self) -> None:
        try:
            await original(self)
        except asyncio.CancelledError:
            # Restore eligibility for the caller's cleanup retry.
            self._closed = False
            raise

    async def defer_commit(self) -> None:
        if not self._closed:
            async with Trace("response_closed", h11_module.logger, self._request):
                await self._connection._response_closed()
            # Commit the idempotence bit ONLY after the underlying state changed.
            self._closed = True

    async def singleflight_commit(self) -> None:
        """Deduplicate concurrent closes; independently finish the state transition.

        The caller may be cancelled while the owned cleanup task continues.
        This is an experimental asyncio-only sketch, not a Trio implementation.
        A real upstream patch must also evaluate lifecycle, exception reporting,
        timeout semantics and AnyIO/Trio compatibility.
        """
        if self._closed:
            return
        in_flight = getattr(self, "_axiomforge_close_in_flight", None)
        if in_flight is None:
            async def complete_once():
                async with Trace("response_closed", h11_module.logger, self._request):
                    await self._connection._response_closed()
                self._closed = True
            in_flight = asyncio.create_task(complete_once())
            self._axiomforge_close_in_flight = in_flight
        try:
            await asyncio.shield(in_flight)
        finally:
            # Clear a completed failed task to allow an actual retry.
            if in_flight.done() and (in_flight.cancelled() or in_flight.exception() is not None):
                self._axiomforge_close_in_flight = None

    implementations = {
        "baseline": original,
        "shield-only": shield_only,
        "rearm-after-cancellation": rearm_after_cancellation,
        "defer-commit": defer_commit,
        "singleflight-commit": singleflight_commit,
    }
    cls.aclose = implementations[strategy]
    try:
        yield
    finally:
        cls.aclose = original
