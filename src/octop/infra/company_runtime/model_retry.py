"""Retry middleware that exposes retry progress to the company runtime stream."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware._retry import (
    OnFailure,
    RetryOn,
    calculate_delay,
    default_retry_on,
    should_retry_exception,
)
from langgraph.errors import GraphBubbleUp


class EmittingModelRetryMiddleware(ModelRetryMiddleware):
    """Model retry middleware with a callback before each retry delay."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        retry_on: RetryOn = default_retry_on,
        on_failure: OnFailure = "continue",
        backoff_factor: float = 2.0,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
        on_retry: Callable[[Exception, int, int], None] | None = None,
    ) -> None:
        super().__init__(
            max_retries=max_retries,
            retry_on=retry_on,
            on_failure=on_failure,
            backoff_factor=backoff_factor,
            initial_delay=initial_delay,
            max_delay=max_delay,
            jitter=jitter,
        )
        self.on_retry = on_retry

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                attempts_made = attempt + 1
                if not should_retry_exception(exc, self.retry_on):
                    raise
                if attempt < self.max_retries:
                    if self.on_retry is not None:
                        self.on_retry(exc, attempt + 1, self.max_retries)
                    delay = calculate_delay(
                        attempt,
                        backoff_factor=self.backoff_factor,
                        initial_delay=self.initial_delay,
                        max_delay=self.max_delay,
                        jitter=self.jitter,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                else:
                    return self._handle_failure(exc, attempts_made)
        raise RuntimeError("Unexpected: retry loop completed without returning")
