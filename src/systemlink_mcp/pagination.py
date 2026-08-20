"""Pagination helpers shared by the simulated and real backends.

SystemLink list APIs use either continuation tokens (Test Monitor, Products,
Specs, DataFrame tables) or skip/take (Assets, Systems, Files). Callers must
walk every page; returning only the first page is a silent data-loss bug.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 500


def walk_continuation(
    fetch: Callable[..., Any],
    items_field: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    extra_kwargs: dict[str, Any] | None = None,
) -> list[T]:
    """Collect every item from a continuation-token API.

    ``fetch`` must accept ``continuation_token`` and ``take``, and return an
    object with ``items_field`` plus ``continuation_token``.
    """
    kwargs = dict(extra_kwargs or {})
    collected: list[T] = []
    token: str | None = None
    for _ in range(max_pages):
        kwargs["continuation_token"] = token
        kwargs["take"] = page_size
        response = fetch(**kwargs)
        items = getattr(response, items_field, None) or []
        collected.extend(items)
        next_token = getattr(response, "continuation_token", None)
        if not next_token:
            return collected
        if next_token == token:
            raise RuntimeError("Continuation token did not change between pages.")
        token = next_token
    raise RuntimeError(f"Pagination exceeded {max_pages} pages for field {items_field!r}.")


def walk_skip_take(
    fetch: Callable[..., Any],
    items_field: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    extra_kwargs: dict[str, Any] | None = None,
    total_count_field: str = "total_count",
) -> tuple[list[T], int | None]:
    """Collect every item from a skip/take API. Returns (items, total_count)."""
    kwargs = dict(extra_kwargs or {})
    collected: list[T] = []
    reported_total: int | None = None
    skip = 0
    for _ in range(max_pages):
        kwargs["skip"] = skip
        kwargs["take"] = page_size
        response = fetch(**kwargs)
        if reported_total is None:
            reported_total = getattr(response, total_count_field, None)
            if reported_total is None:
                reported_total = getattr(response, "count", None)
        items = getattr(response, items_field, None) or []
        if isinstance(items, dict):
            # Systems API returns data as a list of dicts; keep as-is.
            items = list(items)
        collected.extend(items)
        if not items:
            break
        skip += len(items)
        if reported_total is not None and skip >= reported_total:
            break
        if len(items) < page_size:
            break
    else:
        raise RuntimeError(f"Pagination exceeded {max_pages} pages for field {items_field!r}.")
    return collected, reported_total


def chunks(items: Iterable[T], size: int) -> list[list[T]]:
    buf: list[T] = []
    pages: list[list[T]] = []
    for item in items:
        buf.append(item)
        if len(buf) >= size:
            pages.append(buf)
            buf = []
    if buf:
        pages.append(buf)
    return pages
