"""Time-safe USDⓈ-M close basis from contract-trade and index-price klines (FUNDING_RESEARCH_SPEC.md Bölüm 15).

Derived local definition (NOT an official Binance formula — the official
basis page gives none):
    close_basis      = contract_close - index_close                 (exact)
    close_basis_rate = close_basis / index_close                    (34 sig. digits,
                                                                     ROUND_HALF_EVEN)
for ONE `[open_time, close_time)` interval where both a Binance USDⓈ-M
perpetual contract-trade kline and a Binance USDⓈ-M index-price kline of the
same pair/timeframe exist with exactly that open time. No forward-fill,
nearest-neighbour, interpolation or resampling: a slot missing on either
side yields no observation and is reported as a gap.

Availability: an observation is usable from `available_at = max(contract
availability, index availability)`, each being the candle's
`feature_availability_time` (`open_time + duration`); the interval-start
timestamp is never treated as a publication time. `CloseBasisHistory`
exposes observations only through `available_at <= as_of_time` queries.

Arithmetic uses private `decimal.Context` objects only, so results do not
depend on the process-global decimal context. This is a research feature and
descriptive evidence — index price is not tradable and nothing here is a
basis trade, hedge or PnL.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from itertools import pairwise

from crypto_quant_lab.data_quality.feature_availability import feature_availability_time
from crypto_quant_lab.data_quality.time import is_grid_aligned
from crypto_quant_lab.market_data.binance_usdm_basis import (
    OFFICIAL_BASIS_PERIODS,
    BinanceOfficialBasisRecord,
)
from crypto_quant_lab.market_data.timeframes import candle_duration
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.datasets import (
    CONTRACT_TRADE,
    INDEX_PRICE,
    CandleDataset,
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
    coverage_contains,
)
from crypto_quant_lab.storage.sqlite_codec import datetime_to_epoch_us, epoch_us_to_datetime

CLOSE_BASIS_RATE_PRECISION = 34

_EXACT = Context(
    prec=100,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)
_RATE = Context(
    prec=CLOSE_BASIS_RATE_PRECISION,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def _close_basis(contract_close: Decimal, index_close: Decimal) -> tuple[Decimal, Decimal]:
    if not index_close.is_finite() or index_close <= 0:
        raise ValueError(f"index close must be a finite price > 0, got {index_close}")
    if not contract_close.is_finite() or contract_close <= 0:
        raise ValueError(f"contract close must be a finite price > 0, got {contract_close}")
    basis = _EXACT.subtract(contract_close, index_close)
    return basis, _RATE.divide(basis, index_close)


def _check_dataset_pair(contract_dataset: CandleDataset, index_dataset: CandleDataset) -> None:
    for name, dataset in (("contract_dataset", contract_dataset), ("index_dataset", index_dataset)):
        if not isinstance(dataset, CandleDataset):
            raise TypeError(f"{name} must be a CandleDataset, got {type(dataset).__name__}")
    if contract_dataset.price_kind != CONTRACT_TRADE:
        raise ValueError(
            f"contract_dataset price_kind must be {CONTRACT_TRADE!r}, "
            f"got {contract_dataset.price_kind!r}"
        )
    if index_dataset.price_kind != INDEX_PRICE:
        raise ValueError(
            f"index_dataset price_kind must be {INDEX_PRICE!r}, got {index_dataset.price_kind!r}"
        )
    if contract_dataset.namespace != index_dataset.namespace:
        raise ValueError(
            f"contract namespace {contract_dataset.namespace!r} does not match index "
            f"namespace {index_dataset.namespace!r}"
        )


@dataclass(frozen=True, slots=True)
class CloseBasisObservation:
    """One exactly-aligned close basis observation; re-verified on construction."""

    exchange: str
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    available_at: datetime
    contract_close: Decimal
    index_close: Decimal
    close_basis: Decimal
    close_basis_rate: Decimal
    contract_dataset: CandleDataset
    index_dataset: CandleDataset

    def __post_init__(self) -> None:
        _check_dataset_pair(self.contract_dataset, self.index_dataset)
        if (self.exchange, self.symbol, self.timeframe) != (
            self.contract_dataset.exchange,
            self.contract_dataset.symbol,
            self.contract_dataset.timeframe,
        ):
            raise ValueError("observation identity does not match its datasets")
        for name in ("contract_close", "index_close", "close_basis", "close_basis_rate"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"{name} must be a Decimal")
        open_us = datetime_to_epoch_us(self.open_time)
        duration_us = candle_duration(self.timeframe) // timedelta(microseconds=1)
        if not is_grid_aligned(self.open_time, self.timeframe):
            raise ValueError(f"open_time {self.open_time!r} is not grid-aligned")
        if datetime_to_epoch_us(self.close_time) != open_us + duration_us:
            raise ValueError("close_time must equal open_time + timeframe duration")
        if datetime_to_epoch_us(self.available_at) < open_us + duration_us:
            raise ValueError("available_at cannot precede the interval close")
        basis, rate = _close_basis(self.contract_close, self.index_close)
        if self.close_basis != basis or self.close_basis_rate != rate:
            raise ValueError("close_basis/close_basis_rate do not match the close prices")


def compute_close_basis_observation(
    contract: HistoricalCandle,
    index: HistoricalCandle,
    *,
    contract_dataset: CandleDataset,
    index_dataset: CandleDataset,
) -> CloseBasisObservation:
    """Pair exactly one contract-trade and one index-price candle of the same interval."""
    _check_dataset_pair(contract_dataset, index_dataset)
    for name, record, dataset in (
        ("contract", contract, contract_dataset),
        ("index", index, index_dataset),
    ):
        if not isinstance(record, HistoricalCandle):
            raise TypeError(f"{name} must be a HistoricalCandle, got {type(record).__name__}")
        namespace = (
            record.exchange,
            record.market_type,
            record.candle.symbol,
            record.candle.timeframe,
        )
        if namespace != dataset.namespace:
            raise ValueError(f"{name} candle {namespace!r} does not match {dataset.namespace!r}")
    if datetime_to_epoch_us(contract.candle.open_time) != datetime_to_epoch_us(
        index.candle.open_time
    ):
        raise ValueError(
            f"misaligned intervals: contract open {contract.candle.open_time!r} != "
            f"index open {index.candle.open_time!r}"
        )
    basis, rate = _close_basis(contract.candle.close, index.candle.close)
    contract_available = feature_availability_time(contract.candle)
    index_available = feature_availability_time(index.candle)
    return CloseBasisObservation(
        exchange=contract_dataset.exchange,
        symbol=contract_dataset.symbol,
        timeframe=contract_dataset.timeframe,
        open_time=epoch_us_to_datetime(datetime_to_epoch_us(contract.candle.open_time)),
        close_time=contract_available,
        available_at=max(contract_available, index_available),
        contract_close=contract.candle.close,
        index_close=index.candle.close,
        close_basis=basis,
        close_basis_rate=rate,
        contract_dataset=contract_dataset,
        index_dataset=index_dataset,
    )


@dataclass(frozen=True, slots=True)
class CloseBasisPairing:
    """Observations over a grid range plus every slot that could not be paired."""

    observations: tuple[CloseBasisObservation, ...]
    contract_only_open_times: tuple[datetime, ...]
    index_only_open_times: tuple[datetime, ...]
    both_missing_open_times: tuple[datetime, ...]


def _index_records(
    records: Sequence[HistoricalCandle],
    dataset: CandleDataset,
    name: str,
    start_us: int,
    end_us: int,
) -> dict[int, HistoricalCandle]:
    by_open: dict[int, HistoricalCandle] = {}
    for record in records:
        if not isinstance(record, HistoricalCandle):
            raise TypeError(f"{name} records must be HistoricalCandle instances")
        namespace = (
            record.exchange,
            record.market_type,
            record.candle.symbol,
            record.candle.timeframe,
        )
        if namespace != dataset.namespace:
            raise ValueError(f"{name} candle {namespace!r} does not match {dataset.namespace!r}")
        if not is_grid_aligned(record.candle.open_time, dataset.timeframe):
            raise ValueError(f"{name} candle open_time {record.candle.open_time!r} is misaligned")
        open_us = datetime_to_epoch_us(record.candle.open_time)
        if not start_us <= open_us < end_us:
            raise ValueError(f"{name} candle {record.candle.open_time!r} is outside the range")
        if open_us in by_open:
            raise ValueError(f"duplicate {name} candle at {record.candle.open_time!r}")
        by_open[open_us] = record
    return by_open


def pair_close_basis(
    contract_records: Sequence[HistoricalCandle],
    index_records: Sequence[HistoricalCandle],
    *,
    contract_dataset: CandleDataset,
    index_dataset: CandleDataset,
    start_time: datetime,
    end_time: datetime,
) -> CloseBasisPairing:
    """Exact-time pairing over the grid-aligned `[start_time, end_time)`; input order is irrelevant."""
    _check_dataset_pair(contract_dataset, index_dataset)
    timeframe = contract_dataset.timeframe
    if not (is_grid_aligned(start_time, timeframe) and is_grid_aligned(end_time, timeframe)):
        raise ValueError(f"start_time/end_time must be aligned to the {timeframe!r} grid")
    start_us = datetime_to_epoch_us(start_time)
    end_us = datetime_to_epoch_us(end_time)
    if start_us >= end_us:
        raise ValueError("start_time must be strictly before end_time")
    contracts = _index_records(contract_records, contract_dataset, "contract", start_us, end_us)
    indexes = _index_records(index_records, index_dataset, "index", start_us, end_us)

    duration_us = candle_duration(timeframe) // timedelta(microseconds=1)
    observations: list[CloseBasisObservation] = []
    contract_only: list[datetime] = []
    index_only: list[datetime] = []
    both_missing: list[datetime] = []
    for slot_us in range(start_us, end_us, duration_us):
        contract = contracts.get(slot_us)
        index = indexes.get(slot_us)
        if contract is not None and index is not None:
            observations.append(
                compute_close_basis_observation(
                    contract,
                    index,
                    contract_dataset=contract_dataset,
                    index_dataset=index_dataset,
                )
            )
        elif contract is not None:
            contract_only.append(epoch_us_to_datetime(slot_us))
        elif index is not None:
            index_only.append(epoch_us_to_datetime(slot_us))
        else:
            both_missing.append(epoch_us_to_datetime(slot_us))
    return CloseBasisPairing(
        observations=tuple(observations),
        contract_only_open_times=tuple(contract_only),
        index_only_open_times=tuple(index_only),
        both_missing_open_times=tuple(both_missing),
    )


class CloseBasisHistory:
    """Immutable, as-of-gated view of close basis observations over a covered range.

    Observations are only reachable through `visible_at`/`latest_at`, which
    return exactly those with `available_at <= as_of_time` (boundary
    inclusive). `as_of_time` outside `[coverage_start, coverage_end]` raises —
    later observations are unknown, not absent.
    """

    __slots__ = ("_coverage_end", "_coverage_start", "_observations", "_pairing")

    def __init__(
        self, *, coverage_start: datetime, coverage_end: datetime, pairing: CloseBasisPairing
    ) -> None:
        if not isinstance(pairing, CloseBasisPairing):
            raise TypeError(f"pairing must be a CloseBasisPairing, got {type(pairing).__name__}")
        start_us = datetime_to_epoch_us(coverage_start)
        end_us = datetime_to_epoch_us(coverage_end)
        if start_us >= end_us:
            raise ValueError("coverage_start must be strictly before coverage_end")
        keyed = sorted(pairing.observations, key=lambda item: datetime_to_epoch_us(item.open_time))
        for previous, current in pairwise(keyed):
            if datetime_to_epoch_us(previous.open_time) == datetime_to_epoch_us(current.open_time):
                raise ValueError(f"duplicate observation at {current.open_time!r}")
        if keyed:
            first = keyed[0]
            for observation in keyed:
                if (observation.contract_dataset, observation.index_dataset) != (
                    first.contract_dataset,
                    first.index_dataset,
                ):
                    raise ValueError("observations from different provenance cannot be mixed")
                if (
                    datetime_to_epoch_us(observation.open_time) < start_us
                    or datetime_to_epoch_us(observation.close_time) > end_us
                ):
                    raise ValueError(f"observation {observation.open_time!r} is outside coverage")
        self._coverage_start = coverage_start
        self._coverage_end = coverage_end
        self._observations = tuple(keyed)
        self._pairing = pairing

    @property
    def coverage_start(self) -> datetime:
        return self._coverage_start

    @property
    def coverage_end(self) -> datetime:
        return self._coverage_end

    @property
    def contract_only_open_times(self) -> tuple[datetime, ...]:
        return self._pairing.contract_only_open_times

    @property
    def index_only_open_times(self) -> tuple[datetime, ...]:
        return self._pairing.index_only_open_times

    @property
    def both_missing_open_times(self) -> tuple[datetime, ...]:
        return self._pairing.both_missing_open_times

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, "_pairing"):
            raise AttributeError("CloseBasisHistory is immutable")
        object.__setattr__(self, name, value)

    def _check_as_of(self, as_of_time: datetime) -> int:
        as_of_us = datetime_to_epoch_us(as_of_time)
        if not (
            datetime_to_epoch_us(self._coverage_start)
            <= as_of_us
            <= datetime_to_epoch_us(self._coverage_end)
        ):
            raise ValueError(
                f"as_of_time {as_of_time!r} is outside coverage "
                f"[{self._coverage_start!r}, {self._coverage_end!r}]"
            )
        return as_of_us

    def visible_at(self, as_of_time: datetime) -> tuple[CloseBasisObservation, ...]:
        as_of_us = self._check_as_of(as_of_time)
        return tuple(
            item
            for item in self._observations
            if datetime_to_epoch_us(item.available_at) <= as_of_us
        )

    def latest_at(self, as_of_time: datetime) -> CloseBasisObservation | None:
        visible = self.visible_at(as_of_time)
        return visible[-1] if visible else None


def load_close_basis_history(
    contract_store: object,
    index_store: object,
    *,
    symbol: str,
    timeframe: str,
    start_time: datetime,
    end_time: datetime,
) -> CloseBasisHistory:
    """Load and pair both provenance-registered stores over a fully covered range.

    Each store must register exactly the canonical Binance USDⓈ-M dataset
    (contract-trade / index-price of `symbol`) and cover all of
    `[start_time, end_time)`; otherwise ValueError before anything is paired.
    """
    if contract_store is index_store:
        raise ValueError("contract-trade and index-price data must come from separate stores")
    contract_dataset = binance_usdm_perpetual_contract_trade_dataset(symbol, timeframe)
    index_dataset = binance_usdm_index_price_dataset(symbol, timeframe)
    for name, store, expected in (
        ("contract_store", contract_store, contract_dataset),
        ("index_store", index_store, index_dataset),
    ):
        for method in ("query", "query_dataset", "query_coverage"):
            if not callable(getattr(store, method, None)):
                raise TypeError(f"{name} must provide {method}")
        registered = store.query_dataset(*expected.namespace)
        if registered != expected:
            raise ValueError(
                f"{name} namespace {expected.namespace!r} is registered as {registered!r}; "
                f"expected {expected!r}"
            )
        intervals = store.query_coverage(*expected.namespace, start_time, end_time)
        if not coverage_contains(intervals, start_time, end_time):
            raise ValueError(f"{name} coverage does not contain [{start_time!r}, {end_time!r})")
    pairing = pair_close_basis(
        contract_store.query(*contract_dataset.namespace, start_time, end_time),
        index_store.query(*index_dataset.namespace, start_time, end_time),
        contract_dataset=contract_dataset,
        index_dataset=index_dataset,
        start_time=start_time,
        end_time=end_time,
    )
    return CloseBasisHistory(coverage_start=start_time, coverage_end=end_time, pairing=pairing)


@dataclass(frozen=True, slots=True)
class OfficialBasisComparison:
    """Derived close basis vs. official records, matched by `close_time == timestamp`.

    Semantic difference (recorded, not hidden): the official record at T is a
    snapshot at T (live: equal to the klines' OPEN at T), our observation is
    the close of `[T - d, T)`. Differences are therefore expected and are
    measured, never forced to zero. `rate_difference = close_basis_rate -
    official basis / official indexPrice` (34 digits); a timestamp exceeds
    when `|rate_difference| > rate_tolerance`.
    """

    rate_tolerance: Decimal
    comparable_count: int
    official_only_timestamps: tuple[datetime, ...]
    observation_only_close_times: tuple[datetime, ...]
    max_abs_basis_difference: Decimal | None
    mean_abs_basis_difference: Decimal | None
    max_abs_rate_difference: Decimal | None
    mean_abs_rate_difference: Decimal | None
    exceeding_timestamps: tuple[datetime, ...]


def compare_with_official_basis(
    observations: Sequence[CloseBasisObservation],
    records: Sequence[BinanceOfficialBasisRecord],
    *,
    rate_tolerance: Decimal,
) -> OfficialBasisComparison:
    if not isinstance(rate_tolerance, Decimal) or not rate_tolerance.is_finite():
        raise TypeError("rate_tolerance must be a finite Decimal")
    if rate_tolerance < 0:
        raise ValueError("rate_tolerance must be >= 0")
    by_close: dict[int, CloseBasisObservation] = {}
    for item in observations:
        key = datetime_to_epoch_us(item.close_time)
        if key in by_close:
            raise ValueError(f"duplicate observation closing at {item.close_time!r}")
        by_close[key] = item
    by_time: dict[int, BinanceOfficialBasisRecord] = {}
    for record in records:
        key = datetime_to_epoch_us(record.timestamp)
        if key in by_time:
            raise ValueError(f"duplicate official record at {record.timestamp!r}")
        by_time[key] = record
    identities = {(item.symbol, candle_duration(item.timeframe)) for item in by_close.values()}
    identities |= {
        (record.pair, OFFICIAL_BASIS_PERIODS[record.period]) for record in by_time.values()
    }
    if len(identities) > 1:
        raise ValueError(f"mismatched pair/period between observations and records: {identities}")
    if any(record.contract_type != "PERPETUAL" for record in by_time.values()):
        raise ValueError("official records must be PERPETUAL records")

    basis_diffs: list[Decimal] = []
    rate_diffs: list[Decimal] = []
    exceeding: list[datetime] = []
    for key in sorted(by_close.keys() & by_time.keys()):
        item = by_close[key]
        record = by_time[key]
        basis_diff = _EXACT.subtract(item.close_basis, record.basis).copy_abs()
        official_rate = _RATE.divide(record.basis, record.index_price)
        rate_diff = _RATE.subtract(item.close_basis_rate, official_rate).copy_abs()
        basis_diffs.append(basis_diff)
        rate_diffs.append(rate_diff)
        if rate_diff > rate_tolerance:
            exceeding.append(record.timestamp)

    def _mean(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        total = Decimal(0)
        for value in values:
            total = _EXACT.add(total, value)
        return _RATE.divide(total, Decimal(len(values)))

    return OfficialBasisComparison(
        rate_tolerance=rate_tolerance,
        comparable_count=len(rate_diffs),
        official_only_timestamps=tuple(
            by_time[key].timestamp for key in sorted(by_time.keys() - by_close.keys())
        ),
        observation_only_close_times=tuple(
            by_close[key].close_time for key in sorted(by_close.keys() - by_time.keys())
        ),
        max_abs_basis_difference=max(basis_diffs) if basis_diffs else None,
        mean_abs_basis_difference=_mean(basis_diffs),
        max_abs_rate_difference=max(rate_diffs) if rate_diffs else None,
        mean_abs_rate_difference=_mean(rate_diffs),
        exceeding_timestamps=tuple(exceeding),
    )
