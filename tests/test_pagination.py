from __future__ import annotations

from systemlink_mcp.simulate import SimulatedBackend


def test_simulated_multi_page_response_is_fully_traversed() -> None:
    backend = SimulatedBackend(seed=42, page_size=2)
    payload = backend.query_results(
        part_number=None,
        program_name=None,
        serial_number=None,
        status=None,
        started_after=None,
        started_before=None,
        preview_limit=20,
    )
    assert backend.pages_fetched > 1
    assert payload.matched == len(backend.results)
    assert payload.counts.total == len(backend.results)


def test_yield_counts_match_full_traversal() -> None:
    small_pages = SimulatedBackend(seed=42, page_size=3)
    large_pages = SimulatedBackend(seed=42, page_size=1000)
    kwargs = dict(
        part_number="PN-5164-B",
        program_name=None,
        serial_number=None,
        status=None,
        started_after=None,
        started_before=None,
        group_by="none",
    )
    a = small_pages.summarize_yield(**kwargs)
    b = large_pages.summarize_yield(**kwargs)
    assert small_pages.pages_fetched > 1
    assert a.overall.total == b.overall.total
    assert a.yield_percent == b.yield_percent
