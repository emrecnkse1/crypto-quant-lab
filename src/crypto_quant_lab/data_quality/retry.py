"""Bounded connection retry — generic wrapper, no sleep/backoff, no wall-clock.

(DATA_QUALITY_SPEC.md Bölüm 16: retry is bounded and used only for
transient transport/network failures — never for malformed/inconsistent
data.)

`with_connection_retry` wraps any callable and retries it only on
`ConnectionError`, up to `max_attempts` total calls (the first call
included). Retry is immediate and deterministic — no sleep, no backoff, no
jitter, no wall-clock read. Every other exception (including other
`Exception` subclasses and `BaseException` types) propagates on its first
occurrence without a second call.
"""

import functools
from collections.abc import Callable


def with_connection_retry[**P, T](
    operation: Callable[P, T],
    *,
    max_attempts: int = 3,
) -> Callable[P, T]:
    """Wrap `operation` to retry it on `ConnectionError`, up to `max_attempts` total calls."""
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise TypeError(f"max_attempts must be an int, got {type(max_attempts).__name__}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    @functools.wraps(operation)
    def _wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        attempt = 1
        while True:
            try:
                return operation(*args, **kwargs)
            except ConnectionError:
                if attempt >= max_attempts:
                    raise
                attempt += 1

    return _wrapped
