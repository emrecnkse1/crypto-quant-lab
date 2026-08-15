from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from crypto_quant_lab.funding.models import FundingEvent, HistoricalFundingEvent


class _BrokenTzInfo(tzinfo):
    """A tzinfo that pretends to be attached but reports no offset (pseudo-naive)."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return None


def make_funding_event(**overrides):
    fields = {
        "event_time": datetime(2024, 1, 1, tzinfo=UTC),
        "funding_rate": Decimal("0.0001"),
        "reference_price": Decimal(50000),
        "rate_type": "Regular",
    }
    fields.update(overrides)
    return FundingEvent(**fields)


def make_historical_funding_event(**overrides):
    fields = {
        "exchange": "binance",
        "market_type": "perpetual",
        "symbol": "BTCUSDT",
        "funding": make_funding_event(),
    }
    fields.update(overrides)
    return HistoricalFundingEvent(**fields)


# --- FundingEvent: valid construction ---


def test_valid_funding_event_can_be_created():
    event = make_funding_event()
    assert event.event_time == datetime(2024, 1, 1, tzinfo=UTC)
    assert event.funding_rate == Decimal("0.0001")
    assert event.reference_price == Decimal(50000)
    assert event.rate_type == "Regular"


# --- FundingEvent: event_time validation ---


def test_naive_event_time_is_rejected():
    with pytest.raises(ValueError):
        make_funding_event(event_time=datetime(2024, 1, 1))  # noqa: DTZ001


def test_pseudo_naive_event_time_is_rejected():
    with pytest.raises(ValueError):
        make_funding_event(event_time=datetime(2024, 1, 1, tzinfo=_BrokenTzInfo()))


def test_non_datetime_event_time_is_rejected():
    with pytest.raises(TypeError):
        make_funding_event(event_time="2024-01-01")


def test_non_utc_aware_equivalent_event_time_is_accepted():
    plus_five = timezone(timedelta(hours=5))
    local_time = datetime(2024, 1, 1, 5, 0, tzinfo=plus_five)  # == 2024-01-01 00:00 UTC
    event = make_funding_event(event_time=local_time)
    assert event.event_time == local_time
    assert event.event_time.tzinfo is plus_five  # not silently converted/replaced


# --- FundingEvent: funding_rate validation ---


@pytest.mark.parametrize(
    "rate", [Decimal("0.001"), Decimal(0), Decimal("-0.001")], ids=["positive", "zero", "negative"]
)
def test_funding_rate_sign_all_legal(rate):
    event = make_funding_event(funding_rate=rate)
    assert event.funding_rate == rate


@pytest.mark.parametrize("invalid_rate", [1.0, 1, True, "0.001", None])
def test_funding_rate_rejects_invalid_type(invalid_rate):
    with pytest.raises(TypeError, match="funding_rate"):
        make_funding_event(funding_rate=invalid_rate)


@pytest.mark.parametrize(
    "non_finite_rate", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_funding_rate_rejects_non_finite(non_finite_rate):
    with pytest.raises(ValueError, match="finite"):
        make_funding_event(funding_rate=non_finite_rate)


# --- FundingEvent: reference_price validation ---


@pytest.mark.parametrize(
    "price", [Decimal("0.00000001"), Decimal(50000), Decimal("102345.67890123")]
)
def test_reference_price_positive_legal(price):
    event = make_funding_event(reference_price=price)
    assert event.reference_price == price


def test_reference_price_zero_is_rejected():
    with pytest.raises(ValueError, match="reference_price"):
        make_funding_event(reference_price=Decimal(0))


def test_reference_price_negative_is_rejected():
    with pytest.raises(ValueError, match="reference_price"):
        make_funding_event(reference_price=Decimal(-1))


@pytest.mark.parametrize(
    "non_finite_price", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
def test_reference_price_rejects_non_finite(non_finite_price):
    with pytest.raises(ValueError, match="finite"):
        make_funding_event(reference_price=non_finite_price)


@pytest.mark.parametrize("invalid_price", [50000.0, 50000, True, "50000", None])
def test_reference_price_rejects_invalid_type(invalid_price):
    with pytest.raises(TypeError, match="reference_price"):
        make_funding_event(reference_price=invalid_price)


# --- FundingEvent: rate_type validation ---


def test_rate_type_empty_is_rejected():
    with pytest.raises(ValueError, match="rate_type"):
        make_funding_event(rate_type="")


def test_rate_type_whitespace_only_is_rejected():
    with pytest.raises(ValueError, match="rate_type"):
        make_funding_event(rate_type="   ")


def test_rate_type_non_str_is_rejected():
    with pytest.raises(TypeError, match="rate_type"):
        make_funding_event(rate_type=123)


def test_rate_type_is_preserved_exactly_not_normalized():
    event = make_funding_event(rate_type=" Regular ")
    assert event.rate_type == " Regular "


def test_rate_type_is_not_binance_specific():
    """Canonical FundingEvent is exchange-neutral: any non-empty rate_type is legal."""
    event = make_funding_event(rate_type="standard")
    assert event.rate_type == "standard"


# --- FundingEvent: Decimal exactness ---


def test_decimal_precision_preserved_exactly():
    event = make_funding_event(
        funding_rate=Decimal("0.000123456789"),
        reference_price=Decimal("102345.67890123"),
    )
    assert event.funding_rate == Decimal("0.000123456789")
    assert event.reference_price == Decimal("102345.67890123")
    assert str(event.funding_rate) == "0.000123456789"
    assert str(event.reference_price) == "102345.67890123"


# --- FundingEvent: immutability ---


def test_funding_event_is_frozen():
    event = make_funding_event()
    with pytest.raises(FrozenInstanceError):
        event.funding_rate = Decimal("0.002")


# --- HistoricalFundingEvent: valid construction ---


def test_valid_historical_funding_event_can_be_created():
    record = make_historical_funding_event()
    assert record.exchange == "binance"
    assert record.market_type == "perpetual"
    assert record.symbol == "BTCUSDT"
    assert record.funding == make_funding_event()


# --- HistoricalFundingEvent: metadata validation ---


@pytest.mark.parametrize("field_name", ["exchange", "market_type", "symbol"])
def test_empty_metadata_field_is_rejected(field_name):
    with pytest.raises(ValueError, match=field_name):
        make_historical_funding_event(**{field_name: ""})


@pytest.mark.parametrize("field_name", ["exchange", "market_type", "symbol"])
def test_whitespace_only_metadata_field_is_rejected(field_name):
    with pytest.raises(ValueError, match=field_name):
        make_historical_funding_event(**{field_name: "   "})


@pytest.mark.parametrize("field_name", ["exchange", "market_type", "symbol"])
def test_non_str_metadata_field_is_rejected(field_name):
    with pytest.raises(TypeError, match=field_name):
        make_historical_funding_event(**{field_name: 123})


def test_funding_field_must_be_genuine_funding_event():
    with pytest.raises(TypeError, match="funding"):
        make_historical_funding_event(funding={"event_time": datetime(2024, 1, 1, tzinfo=UTC)})


# --- HistoricalFundingEvent: canonical_key ---


def test_canonical_key_contains_correct_fields_in_order():
    funding = make_funding_event()
    record = make_historical_funding_event(funding=funding)

    assert record.canonical_key == (
        "binance",
        "perpetual",
        "BTCUSDT",
        funding.event_time,
        funding.rate_type,
    )


def test_canonical_key_excludes_payload_fields():
    funding = make_funding_event()
    record = make_historical_funding_event(funding=funding)

    assert funding.funding_rate not in record.canonical_key
    assert funding.reference_price not in record.canonical_key


# --- HistoricalFundingEvent: same-timestamp multi-type support ---


def test_same_timestamp_different_rate_type_both_valid_with_distinct_keys():
    same_time = datetime(2024, 1, 1, 8, 0, tzinfo=UTC)

    event_a = make_historical_funding_event(
        funding=make_funding_event(event_time=same_time, rate_type="Regular")
    )
    event_b = make_historical_funding_event(
        funding=make_funding_event(event_time=same_time, rate_type="Special")
    )

    assert event_a.funding.event_time == event_b.funding.event_time
    assert event_a.canonical_key != event_b.canonical_key
    assert event_a.funding.rate_type == "Regular"
    assert event_b.funding.rate_type == "Special"


# --- HistoricalFundingEvent: immutability ---


def test_historical_funding_event_is_frozen():
    record = make_historical_funding_event()
    with pytest.raises(FrozenInstanceError):
        record.exchange = "other"
