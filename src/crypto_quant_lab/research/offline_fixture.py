"""Small synthetic offline fixture for end-to-end research smoke runs (FUNDING_RESEARCH_SPEC.md Bölüm 17.4).

Everything is written through the PRODUCTION ingestion paths (contract-trade
klines, index-price klines, settled funding) with in-memory fake page
fetchers, so provenance and coverage are registered exactly as for real data.
Nothing touches the network or any existing database: the target directory
must not exist yet.

Synthetic market (symbol "FIXTUREUSDT", 1h, 24 candles from FIXTURE_START):
- contract OPEN is 100 for every hour; contract CLOSE: h0 100.5, h1 99,
  h2 100, every other hour 100.1;
- index CLOSE 100 for every hour except hour 9, which is ABSENT (a gap);
- settled funding every 8h (markPrice 100): -8h 0.0001, 0h 0.0003,
  8h 0.00005, 16h 0.0001.

Hand-derived expectations (EXPECTED_* below), for the pre-fixed candidate
short >= 0.0002 / long <= -0.0001 / max age 9h / lag 60s, qty 1, cash 1000,
commission 0.001 per fill on notional:
- close basis: 23 observations; h0 +0.5 (rate 0.005), h1 -1 (-0.01),
  h2 0 (0), 20 hours +0.1 (0.001); hour 9 reported as contract-only gap;
  premium 21, discount 1, zero 1;
- window A [0h, 12h): the 0h event (0.0003) is visible at the first decision
  (1h) -> SHORT, filled at h1 open 100; the 8h event (0.00005, neutral) is
  visible from 8h+60s, so the 9h decision is FLAT, filled at h9 open 100.
  2 fills; price PnL 0; funding at 8h while short: cost = -1 * 100 * 0.00005
  = -0.005 (an inflow); commission 2 * 0.1 = 0.2 -> final equity 999.805;
- window B [12h, 24h): visible rates 0.00005 then 0.0001, both inside the
  neutral band -> 0 fills, final equity 1000;
- no-trade control: 0 fills, 1000 in both windows.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from crypto_quant_lab.data_quality.usdm_ingestion import (
    ingest_binance_usdm_index_price_klines,
    ingest_binance_usdm_perpetual_klines,
)
from crypto_quant_lab.funding.binance import parse_binance_historical_funding_record
from crypto_quant_lab.funding.ingestion import ingest_binance_historical_funding_range
from crypto_quant_lab.funding.sqlite import SQLiteHistoricalFundingStore
from crypto_quant_lab.market_data.binance_usdm import (
    parse_binance_usdm_index_price_kline,
    parse_binance_usdm_kline,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

FIXTURE_SYMBOL = "FIXTUREUSDT"
FIXTURE_TIMEFRAME = "1h"
FIXTURE_START = datetime(2026, 1, 5, tzinfo=UTC)
FIXTURE_HOURS = 24
FIXTURE_AS_OF = FIXTURE_START + timedelta(days=2)
FIXTURE_MISSING_INDEX_HOUR = 9
_HOUR = timedelta(hours=1)
_HOUR_MS = 3_600_000
_START_MS = int(FIXTURE_START.timestamp()) * 1000
_CONTRACT_CLOSES = {0: "100.5", 1: "99", 2: "100"}
_FUNDING = ((-8, "0.0001"), (0, "0.0003"), (8, "0.00005"), (16, "0.0001"))

EXPECTED_BASIS = {
    "paired_observation_count": 23,
    "contract_only_open_times": [FIXTURE_START + _HOUR * FIXTURE_MISSING_INDEX_HOUR],
    "premium_count": 21,
    "discount_count": 1,
    "zero_count": 1,
    "close_basis_min": Decimal(-1),
    "close_basis_max": Decimal("0.5"),
}
EXPECTED_CARRY_WINDOWS = (
    {"fill_count": 2, "final_equity": Decimal("999.805")},
    {"fill_count": 0, "final_equity": Decimal(1000)},
)
EXPECTED_CONTROL_WINDOWS = (
    {"fill_count": 0, "final_equity": Decimal(1000)},
    {"fill_count": 0, "final_equity": Decimal(1000)},
)


def _contract_row(hour: int) -> list[object]:
    open_ms = _START_MS + hour * _HOUR_MS
    close = _CONTRACT_CLOSES.get(hour, "100.1")
    high = max("100", close, key=Decimal)
    low = min("100", close, key=Decimal)
    return [
        open_ms,
        "100",
        high,
        low,
        close,
        "1",
        open_ms + _HOUR_MS - 1,
        "100",
        1,
        "0.5",
        "50",
        "0",
    ]


def _index_row(hour: int) -> list[object]:
    open_ms = _START_MS + hour * _HOUR_MS
    return [
        open_ms,
        "100",
        "100",
        "100",
        "100",
        "0",
        open_ms + _HOUR_MS - 1,
        "0",
        3600,
        "0",
        "0",
        "0",
    ]


def _page_fetcher(rows: list[list[object]], parse):
    def fetch(*, start_time_ms: int, end_time_ms: int):
        return [
            parse(row, FIXTURE_SYMBOL, FIXTURE_TIMEFRAME)
            for row in rows
            if start_time_ms <= row[0] <= end_time_ms
        ]

    return fetch


def _funding_fetcher(symbol, start_ms, end_ms, *, timeout, max_attempts):
    return [
        parse_binance_historical_funding_record(
            {
                "symbol": FIXTURE_SYMBOL,
                "fundingRate": rate,
                "fundingTime": _START_MS + hour * _HOUR_MS,
                "markPrice": "100",
                "rateType": "Regular",
            },
            expected_symbol=FIXTURE_SYMBOL,
        )
        for hour, rate in _FUNDING
        if start_ms <= _START_MS + hour * _HOUR_MS <= end_ms
    ]


@dataclass(frozen=True, slots=True)
class OfflineFixture:
    directory: Path
    config: dict[str, object]


def fixture_config() -> dict[str, object]:
    """The research config (CLI schema v1) for the fixture; store paths are relative."""

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    end = FIXTURE_START + _HOUR * FIXTURE_HOURS
    return {
        "config_version": 1,
        "symbol": FIXTURE_SYMBOL,
        "timeframe": FIXTURE_TIMEFRAME,
        "start": iso(FIXTURE_START),
        "end": iso(end),
        "as_of": iso(FIXTURE_AS_OF),
        "stores": {"contract": "contract.db", "index": "index.db", "funding": "funding.db"},
        "funding_research": {
            "funding_coverage_start": iso(FIXTURE_START - _HOUR * 8),
            "funding_coverage_end": iso(end),
            "publication_lag_seconds": 60,
            "windows": [
                [iso(FIXTURE_START), iso(FIXTURE_START + _HOUR * 12)],
                [iso(FIXTURE_START + _HOUR * 12), iso(end)],
            ],
            "initial_cash": "1000",
            "position_quantity": "1",
            "candidate": {
                "candidate_id": "carry_s2bp_l-1bp",
                "short_entry_rate": "0.0002",
                "long_entry_rate": "-0.0001",
                "max_funding_age_hours": 9,
            },
            "include_no_trade_control": True,
            "cost": {"commission_rate": "0.001", "half_spread_rate": "0", "slippage_rate": "0"},
        },
    }


def build_offline_fixture(directory: Path) -> OfflineFixture:
    """Create the three fixture stores in a NEW directory through production ingestion."""
    directory = Path(directory)
    directory.mkdir(parents=False, exist_ok=False)
    end = FIXTURE_START + _HOUR * FIXTURE_HOURS
    hours = range(FIXTURE_HOURS)
    contract = SQLiteHistoricalCandleStore(directory / "contract.db")
    index = SQLiteHistoricalCandleStore(directory / "index.db")
    funding = SQLiteHistoricalFundingStore(directory / "funding.db")
    try:
        ingest_binance_usdm_perpetual_klines(
            contract,
            symbol=FIXTURE_SYMBOL,
            timeframe=FIXTURE_TIMEFRAME,
            requested_start=FIXTURE_START,
            requested_end=end,
            as_of_time=FIXTURE_AS_OF,
            fetch_page=_page_fetcher([_contract_row(h) for h in hours], parse_binance_usdm_kline),
        )
        ingest_binance_usdm_index_price_klines(
            index,
            pair=FIXTURE_SYMBOL,
            timeframe=FIXTURE_TIMEFRAME,
            requested_start=FIXTURE_START,
            requested_end=end,
            as_of_time=FIXTURE_AS_OF,
            fetch_page=_page_fetcher(
                [_index_row(h) for h in hours if h != FIXTURE_MISSING_INDEX_HOUR],
                parse_binance_usdm_index_price_kline,
            ),
        )
        ingest_binance_historical_funding_range(
            funding,
            symbol=FIXTURE_SYMBOL,
            market_type="usdm_perpetual",
            requested_start=FIXTURE_START - _HOUR * 8,
            requested_end=end,
            source_as_of=FIXTURE_AS_OF,
            ingestion_as_of=FIXTURE_AS_OF,
            fetch_history=_funding_fetcher,
        )
    finally:
        contract.close()
        index.close()
        funding.close()
    return OfflineFixture(directory=directory, config=fixture_config())
