from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypto_quant_lab.funding import binance_pagination
from crypto_quant_lab.funding.binance import BinanceHistoricalFundingRecord
from crypto_quant_lab.funding.binance_pagination import fetch_binance_funding_history

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _record(
    ms: int,
    rate_type: str = "Regular",
    *,
    symbol: str = "BTCUSDT",
    funding_rate: str = "0.0001",
    mark_price: str = "65000",
) -> BinanceHistoricalFundingRecord:
    return BinanceHistoricalFundingRecord(
        symbol=symbol,
        funding_rate=Decimal(funding_rate),
        funding_time=_EPOCH + timedelta(milliseconds=ms),
        mark_price=Decimal(mark_price),
        rate_type=rate_type,
    )


def _make_fetcher(*outcomes: object):
    calls: list[dict] = []
    outcomes_iter = iter(outcomes)

    def _fetch(symbol, start_time_ms, end_time_ms, *, limit, timeout):
        calls.append(
            {
                "symbol": symbol,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
                "timeout": timeout,
            }
        )
        outcome = next(outcomes_iter)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    _fetch.calls = calls
    return _fetch


def _poison_fetcher(*args, **kwargs):
    raise AssertionError("fetch_page should not be called for invalid input")


# --- normal pagination ---


def test_normal_pagination_full_page_then_short_page(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 3)
    a, b, c, d = _record(1000), _record(2000), _record(3000), _record(4000)
    fetcher = _make_fetcher([a, b, c], [c, d])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b, c, d]
    assert len(fetcher.calls) == 2


def test_empty_first_page_returns_empty():
    fetcher = _make_fetcher([])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == []
    assert len(fetcher.calls) == 1


def test_single_short_page_returns_all_records_with_one_fetch(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 3)
    a, b = _record(1000), _record(2000)
    fetcher = _make_fetcher([a, b])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b]
    assert len(fetcher.calls) == 1


def test_multi_page_cursor_sequence(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a, b, c = _record(1000), _record(2000), _record(3000)
    fetcher = _make_fetcher([a, b], [c])

    fetch_binance_funding_history("BTCUSDT", 500, 9999, fetch_page=fetcher)

    assert [call["start_time_ms"] for call in fetcher.calls] == [500, 2000]
    assert all(call["end_time_ms"] == 9999 for call in fetcher.calls)


def test_never_plus_1ms_regression(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a, b, c = _record(1000), _record(2000), _record(3000)
    fetcher = _make_fetcher([a, b], [c])

    fetch_binance_funding_history("BTCUSDT", 500, 9999, fetch_page=fetcher)

    assert fetcher.calls[1]["start_time_ms"] == 2000


def test_same_time_sibling_preserved_across_restart(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a = _record(1000)
    t_regular = _record(2000, "Regular")
    t_special = _record(2000, "Special")
    fetcher = _make_fetcher([a, t_regular], [t_regular, t_special], [])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, t_regular, t_special]
    assert len(fetcher.calls) == 3


# --- same-page duplicate / conflict ---


def test_same_page_duplicate_identical_payload_raises():
    x1 = _record(1000, funding_rate="0.0001")
    x2 = _record(1000, funding_rate="0.0001")
    fetcher = _make_fetcher([x1, x2])

    with pytest.raises(ValueError, match="duplicate source key"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_same_page_duplicate_differing_payload_raises():
    x1 = _record(1000, funding_rate="0.0001")
    x2 = _record(1000, funding_rate="0.0002")
    fetcher = _make_fetcher([x1, x2])

    with pytest.raises(ValueError, match="duplicate source key"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_same_time_different_type_within_page_is_accepted():
    regular = _record(1000, "Regular")
    special = _record(1000, "Special")
    fetcher = _make_fetcher([regular, special])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [regular, special]


# --- cross-page duplicate / conflict ---


def test_cross_page_identical_duplicate_is_deduped(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a, b, c = _record(1000), _record(2000), _record(3000)
    fetcher = _make_fetcher([a, b], [b, c], [])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b, c]


def test_cross_page_decimal_scale_is_treated_as_identical(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a = _record(1000)
    b_page1 = _record(2000, mark_price="0.001")
    b_page2 = _record(2000, mark_price="0.0010")
    c = _record(3000)
    fetcher = _make_fetcher([a, b_page1], [b_page2, c], [])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b_page1, c]


def test_cross_page_conflict_raises(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a = _record(1000)
    b_page1 = _record(2000, funding_rate="0.0001")
    b_page2 = _record(2000, funding_rate="0.0002")
    c = _record(3000)
    fetcher = _make_fetcher([a, b_page1], [b_page2, c])

    with pytest.raises(ValueError, match="conflicting payload"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


# --- page order ---


def test_decreasing_timestamp_within_page_raises():
    later, earlier = _record(2000), _record(1000)
    fetcher = _make_fetcher([later, earlier])

    with pytest.raises(ValueError, match="not non-decreasing"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_same_time_reversed_type_order_is_legal():
    special = _record(2000, "Special")
    regular = _record(2000, "Regular")
    fetcher = _make_fetcher([special, regular])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [special, regular]


# --- no-progress fail-closed ---


def test_full_page_no_progress_raises(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    b, c = _record(2000, "Regular"), _record(2000, "Special")
    b_dup = _record(2000, "Regular")
    c_dup = _record(2000, "Special")
    fetcher = _make_fetcher([b, c], [b_dup, c_dup])

    with pytest.raises(ValueError, match="no unseen source keys"):
        fetch_binance_funding_history("BTCUSDT", 2000, 9999, fetch_page=fetcher)

    assert len(fetcher.calls) == 2


def test_pathological_same_timestamp_saturation_raises(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    a = _record(5000, "A")
    b = _record(5000, "B")
    a_dup = _record(5000, "A")
    b_dup = _record(5000, "B")
    fetcher = _make_fetcher([a, b], [a_dup, b_dup])

    with pytest.raises(ValueError, match="no unseen source keys"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_full_page_partial_progress_continues(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 3)
    a, b, c = _record(1000), _record(2000), _record(3000)
    c_dup, d, e = _record(3000), _record(4000), _record(5000)
    fetcher = _make_fetcher([a, b, c], [c_dup, d, e], [])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b, c, d, e]
    assert len(fetcher.calls) == 3


def test_terminal_short_page_with_restart_duplicate(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 3)
    a, b, c = _record(1000), _record(2000), _record(3000)
    c_dup, d = _record(3000), _record(4000)
    fetcher = _make_fetcher([a, b, c], [c_dup, d])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b, c, d]
    assert len(fetcher.calls) == 2


# --- injected page defense ---


def test_page_exceeding_page_limit_raises(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 2)
    fetcher = _make_fetcher([_record(1000), _record(2000), _record(3000)])

    with pytest.raises(ValueError, match="_PAGE_LIMIT"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_injected_non_record_raises():
    fetcher = _make_fetcher([{"not": "a record"}])

    with pytest.raises(ValueError, match="BinanceHistoricalFundingRecord"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_injected_symbol_mismatch_raises():
    fetcher = _make_fetcher([_record(1000, symbol="ETHUSDT")])

    with pytest.raises(ValueError, match="symbol mismatch"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_naive_funding_time_raises():
    naive_record = BinanceHistoricalFundingRecord(
        symbol="BTCUSDT",
        funding_rate=Decimal("0.0001"),
        funding_time=datetime(2024, 1, 1),  # noqa: DTZ001
        mark_price=Decimal(65000),
        rate_type="Regular",
    )
    fetcher = _make_fetcher([naive_record])

    with pytest.raises(ValueError, match="timezone-aware"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


def test_sub_millisecond_funding_time_raises():
    sub_ms_record = BinanceHistoricalFundingRecord(
        symbol="BTCUSDT",
        funding_rate=Decimal("0.0001"),
        funding_time=_EPOCH + timedelta(milliseconds=1000, microseconds=500),
        mark_price=Decimal(65000),
        rate_type="Regular",
    )
    fetcher = _make_fetcher([sub_ms_record])

    with pytest.raises(ValueError, match="millisecond-aligned"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)


# --- transport range defense ---


def test_record_behind_current_cursor_raises():
    behind = _record(1999)
    fetcher = _make_fetcher([behind])

    with pytest.raises(ValueError, match="behind requested cursor"):
        fetch_binance_funding_history("BTCUSDT", 2000, 9999, fetch_page=fetcher)


def test_record_beyond_requested_end_raises():
    beyond = _record(5001)
    fetcher = _make_fetcher([beyond])

    with pytest.raises(ValueError, match="exceeds requested end_time_ms"):
        fetch_binance_funding_history("BTCUSDT", 1000, 5000, fetch_page=fetcher)


def test_record_at_current_cursor_is_legal():
    at_cursor = _record(2000)
    fetcher = _make_fetcher([at_cursor])

    result = fetch_binance_funding_history("BTCUSDT", 2000, 9999, fetch_page=fetcher)

    assert result == [at_cursor]


def test_record_at_requested_end_is_legal():
    at_end = _record(5000)
    fetcher = _make_fetcher([at_end])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 5000, fetch_page=fetcher)

    assert result == [at_end]


# --- retry ---


def test_retry_success_on_transient_connection_error():
    a = _record(1000)
    fetcher = _make_fetcher(ConnectionError("boom"), [a])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a]
    assert len(fetcher.calls) == 2
    assert fetcher.calls[0] == fetcher.calls[1]


def test_retry_exhaustion_propagates_connection_error():
    fetcher = _make_fetcher(ConnectionError("e1"), ConnectionError("e2"), ConnectionError("e3"))

    with pytest.raises(ConnectionError, match="e3"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher, max_attempts=3)

    assert len(fetcher.calls) == 3


def test_no_retry_on_value_error():
    fetcher = _make_fetcher(ValueError("malformed"))

    with pytest.raises(ValueError, match="malformed"):
        fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert len(fetcher.calls) == 1


def test_mid_pagination_retry_does_not_refetch_first_page(monkeypatch):
    monkeypatch.setattr(binance_pagination, "_PAGE_LIMIT", 3)
    a, b, c = _record(1000), _record(2000), _record(3000)
    c_dup, d = _record(3000), _record(4000)
    fetcher = _make_fetcher([a, b, c], ConnectionError("transient"), [c_dup, d])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 9999, fetch_page=fetcher)

    assert result == [a, b, c, d]
    assert len(fetcher.calls) == 3
    assert fetcher.calls[0]["start_time_ms"] == 1000
    assert fetcher.calls[1]["start_time_ms"] == 3000
    assert fetcher.calls[2]["start_time_ms"] == 3000


# --- input validation ---


def test_empty_symbol_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="symbol"):
        fetch_binance_funding_history("", 1000, 9999, fetch_page=_poison_fetcher)


def test_whitespace_symbol_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="symbol"):
        fetch_binance_funding_history("   ", 1000, 9999, fetch_page=_poison_fetcher)


def test_non_str_symbol_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="symbol"):
        fetch_binance_funding_history(123, 1000, 9999, fetch_page=_poison_fetcher)


def test_negative_start_time_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_history("BTCUSDT", -1, 9999, fetch_page=_poison_fetcher)


def test_negative_end_time_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="end_time_ms"):
        fetch_binance_funding_history("BTCUSDT", 1000, -1, fetch_page=_poison_fetcher)


def test_bool_start_time_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_history("BTCUSDT", True, 9999, fetch_page=_poison_fetcher)


def test_bool_end_time_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="end_time_ms"):
        fetch_binance_funding_history("BTCUSDT", 1000, False, fetch_page=_poison_fetcher)


def test_start_after_end_is_rejected_before_fetch():
    with pytest.raises(ValueError, match="start_time_ms"):
        fetch_binance_funding_history("BTCUSDT", 2000, 1000, fetch_page=_poison_fetcher)


def test_start_equal_end_is_legal():
    fetcher = _make_fetcher([])

    result = fetch_binance_funding_history("BTCUSDT", 1000, 1000, fetch_page=fetcher)

    assert result == []
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0]["start_time_ms"] == 1000
    assert fetcher.calls[0]["end_time_ms"] == 1000
