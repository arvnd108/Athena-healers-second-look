"""Shared retry policy for transient HTTP failures.

Every gold-standard case opens with a UniProt lookup and an Ensembl lookup, and
running eight cases back to back reliably tripped rate limiting: six of eight
runs failed at validation with a transport error, before any structural work
began. Those are transient — the same request succeeds moments later — but
without a retry each one costs a whole case.

Scope is deliberately narrow. Retries cover **transport errors** (connection
reset, DNS, timeout) and the two status classes that mean "try again" (429 rate
limited, 5xx server error). A 404 is not retried: the record genuinely is not
there, and retrying would turn a clear answer into a slow one.

This does not paper over a service being down. After the final attempt the
original exception propagates, the caller converts it to its own error type, and
the pipeline reports §8's timeout message as before — just after having given a
transient blip a fair chance first.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx

T = TypeVar("T")

#: Total attempts, including the first. Three covers the observed rate-limit
#: blips without turning a real outage into a long hang.
DEFAULT_ATTEMPTS = 3

#: Base for exponential backoff: 0.5s, then 1.0s. Kept short because the pipeline
#: is interactive; the harness adds its own inter-case pacing on top.
DEFAULT_BACKOFF_SECONDS = 0.5

#: Status codes worth retrying. 429 is explicit rate limiting; 5xx is the server
#: failing in a way that is often momentary.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def is_retryable(exc: BaseException) -> bool:
    """True for transport failures and retryable status codes."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def with_retry(
    call: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleeper: Callable[[float], None] | None = None,
) -> T:
    """Run `call`, retrying transient HTTP failures with exponential backoff.

    Re-raises the last exception once attempts are exhausted, so callers keep
    their existing error handling unchanged.
    """
    sleep = sleeper if sleeper is not None else time.sleep
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return call()
        except httpx.HTTPError as exc:
            if not is_retryable(exc) or attempt == attempts - 1:
                raise
            last = exc
            sleep(backoff_seconds * (2**attempt))
    assert last is not None  # unreachable: the loop either returns or raises
    raise last
