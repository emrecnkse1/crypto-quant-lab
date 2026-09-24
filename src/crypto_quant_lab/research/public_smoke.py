"""Opt-in public-data smoke: last 7 closed UTC days, 1h (FUNDING_RESEARCH_SPEC.md Bölüm 18.3).

Per symbol: contract-trade + index-price kline ingestion into stores inside
the run bundle, the derived close basis, Binance's official
`/futures/data/basis` records, their algebraic self-consistency, an
open-snapshot semantics probe and the descriptive close-vs-snapshot
comparison. Nothing here computes a basis formula; production APIs do.

Check contract (report schema v2), per symbol:
- execution      failed on any exception (HTTP/API error incl. rate limit,
                 timeout after bounded retries, parse error, request-budget
                 exhaustion, provenance/coverage failure) -> run "failed";
- data_integrity failed if candles or official records are missing for the
                 requested slots -> run "failed";
- formula        failed if an official record is algebraically inconsistent
                 -> run "failed";
- anomaly        warning if an official record's prices are NOT the contract
                 and index kline OPENs at its timestamp (the observed snapshot
                 semantics does not hold) — unexplained, visible, not a failure;
- descriptive    warning if close-basis vs official-snapshot rate differences
                 exceed the fixed 1 bp tolerance. The two series measure
                 different instants (close of [T-1h, T) vs snapshot at T), so
                 an exceedance alone does not mean the pipeline is broken; the
                 tolerance is never relaxed and every exceedance is listed.
A symbol that fails keeps its error in the report; other symbols' results
are kept, but the run as a whole is "failed" — partial results never look
like a full success.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from crypto_quant_lab.data_quality.usdm_ingestion import (
    ingest_binance_usdm_index_price_klines,
    ingest_binance_usdm_perpetual_klines,
)
from crypto_quant_lab.market_data.binance_usdm import (
    fetch_binance_usdm_index_price_klines,
    fetch_binance_usdm_klines,
    fetch_usdm_json_list,
)
from crypto_quant_lab.market_data.binance_usdm_basis import (
    check_official_basis_consistency,
    fetch_binance_official_basis,
)
from crypto_quant_lab.research.basis import compare_with_official_basis
from crypto_quant_lab.research.cli import (
    BASIS_DOES_NOT_PROVE,
    BASIS_LIMITATIONS,
    Section,
    _sanitize,
    basis_history_and_section,
    open_candle_store,
    parse_config,
    read_stores,
    to_view,
)
from crypto_quant_lab.research.decimal_policy import build_context, normalize_decimal_context
from crypto_quant_lab.research.report import OutputBundle, canonical_json, check, sha256_hex
from crypto_quant_lab.storage.datasets import (
    binance_usdm_index_price_dataset,
    binance_usdm_perpetual_contract_trade_dataset,
)
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore

PUBLIC_SMOKE_RATE_TOLERANCE = Decimal("0.0001")
PUBLIC_SMOKE_TIMEOUT_SECONDS = 10.0
PUBLIC_SMOKE_MAX_ATTEMPTS = 2
PUBLIC_SMOKE_REQUEST_BUDGET_PER_SYMBOL = 6  # 3 endpoints x 2 attempts
_TIMEFRAME = "1h"
_HOUR = timedelta(hours=1)


class RequestBudgetExceeded(RuntimeError):
    """More HTTP requests were needed than the pre-declared budget allows."""


class RequestBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def spend(self, label: str) -> None:
        if self.used >= self.limit:
            raise RequestBudgetExceeded(
                f"request budget of {self.limit} exhausted before {label} request"
            )
        self.used += 1


def _ingest_symbol(workdir, symbol, start, end, now, budget: RequestBudget) -> None:
    contract = SQLiteHistoricalCandleStore(workdir / "contract.db")
    index = SQLiteHistoricalCandleStore(workdir / "index.db")
    try:
        for ingest, store, fetch, key, label in (
            (
                ingest_binance_usdm_perpetual_klines,
                contract,
                fetch_binance_usdm_klines,
                "symbol",
                "contract klines",
            ),
            (
                ingest_binance_usdm_index_price_klines,
                index,
                fetch_binance_usdm_index_price_klines,
                "pair",
                "index klines",
            ),
        ):

            def page(*, start_time_ms, end_time_ms, _fetch=fetch, _label=label):
                budget.spend(_label)
                return _fetch(
                    symbol,
                    _TIMEFRAME,
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    limit=1000,
                    timeout=PUBLIC_SMOKE_TIMEOUT_SECONDS,
                )

            ingest(
                store,
                **{key: symbol},
                timeframe=_TIMEFRAME,
                requested_start=start,
                requested_end=end,
                as_of_time=now,
                fetch_page=page,
                max_attempts=PUBLIC_SMOKE_MAX_ATTEMPTS,
            )
    finally:
        contract.close()
        index.close()


def _opens(workdir, symbol, start, end) -> tuple[dict, dict]:
    """Kline opens by open_time, read-only (stores created by this run only)."""
    contract = open_candle_store(workdir / "contract.db")
    index = open_candle_store(workdir / "index.db")
    with read_stores(contract, index):
        contract_open = {
            r.candle.open_time: r.candle.open
            for r in contract.query(
                *binance_usdm_perpetual_contract_trade_dataset(symbol, _TIMEFRAME).namespace,
                start,
                end,
            )
        }
        index_open = {
            r.candle.open_time: r.candle.open
            for r in index.query(
                *binance_usdm_index_price_dataset(symbol, _TIMEFRAME).namespace, start, end
            )
        }
    return contract_open, index_open


def _run_symbol(bundle, symbol, start, end, now, budget_limit) -> tuple[dict, list, list]:
    budget = RequestBudget(budget_limit)
    workdir = bundle.staging / symbol
    workdir.mkdir()
    _ingest_symbol(workdir, symbol, start, end, now, budget)
    config = parse_config(
        {
            "config_version": 1,
            "symbol": symbol,
            "timeframe": _TIMEFRAME,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "as_of": now.isoformat(),
            "stores": {"contract": "contract.db", "index": "index.db"},
        },
        base_dir=workdir,
    )
    history, basis = basis_history_and_section(config)

    def fetch_rows(url, limit, timeout):
        budget.spend("official basis")
        return fetch_usdm_json_list(url, limit=limit, timeout=timeout)

    official = fetch_binance_official_basis(
        symbol,
        "PERPETUAL",
        _TIMEFRAME,
        start_time=start,
        end_time=end,
        as_of_time=now,
        timeout=PUBLIC_SMOKE_TIMEOUT_SECONDS,
        fetch_rows=fetch_rows,
    )
    contract_open, index_open = _opens(workdir, symbol, start, end)
    consistency = [check_official_basis_consistency(r) for r in official]
    comparison = compare_with_official_basis(
        history.visible_at(end), official, rate_tolerance=PUBLIC_SMOKE_RATE_TOLERANCE
    )
    slots = (end - start) // _HOUR
    results = basis.results
    unpaired = (
        len(results["contract_only_open_times"])
        + len(results["index_only_open_times"])
        + len(results["both_missing_open_times"])
    )
    snapshot_mismatches = [
        r.timestamp
        for r in official
        if (contract_open.get(r.timestamp), index_open.get(r.timestamp))
        != (r.futures_price, r.index_price)
    ]
    inconsistent = [
        r.timestamp for r, c in zip(official, consistency, strict=True) if not c.is_consistent
    ]
    summary = {k: v for k, v in results.items() if k != "observations"}
    summary["request_count"] = budget.used
    summary["official"] = {
        "record_count": len(official),
        "expected_record_count": slots,
        "algebraically_consistent": len(official) - len(inconsistent),
        "algebraically_inconsistent_timestamps": inconsistent,
        "open_snapshot_matches": len(official) - len(snapshot_mismatches),
        "open_snapshot_mismatch_timestamps": snapshot_mismatches,
        "comparable_count": comparison.comparable_count,
        "official_only_timestamps": list(comparison.official_only_timestamps),
        "observation_only_close_times": list(comparison.observation_only_close_times),
        "max_abs_basis_difference": comparison.max_abs_basis_difference,
        "mean_abs_basis_difference": comparison.mean_abs_basis_difference,
        "max_abs_rate_difference": comparison.max_abs_rate_difference,
        "mean_abs_rate_difference": comparison.mean_abs_rate_difference,
        "rate_tolerance": comparison.rate_tolerance,
        "exceeding_timestamps": list(comparison.exceeding_timestamps),
    }
    exceed = len(comparison.exceeding_timestamps)
    checks = [
        check(
            f"{symbol}.execution",
            "passed",
            f"ingestion, pairing and official fetch completed with {budget.used} request(s)",
            category="execution",
        ),
        check(
            f"{symbol}.candles_complete",
            "passed" if unpaired == 0 else "failed",
            f"{results['paired_observation_count']}/{slots} slots paired; {unpaired} unpaired",
            category="data_integrity",
        ),
        check(
            f"{symbol}.official_records_complete",
            "passed" if len(official) == slots else "failed",
            f"{len(official)}/{slots} official records",
            category="data_integrity",
        ),
        check(
            f"{symbol}.official_algebra",
            "passed" if not inconsistent else "failed",
            f"{len(official) - len(inconsistent)}/{len(official)} records satisfy basis = "
            "futuresPrice - indexPrice and |basisRate - basis/indexPrice| <= 0.00005",
            category="formula",
        ),
        check(
            f"{symbol}.official_open_snapshot",
            "passed" if not snapshot_mismatches else "warning",
            f"{len(official) - len(snapshot_mismatches)}/{len(official)} official records equal "
            "the contract/index kline OPEN at their timestamp"
            + ("" if not snapshot_mismatches else " — UNEXPLAINED mismatches listed in results"),
            category="anomaly",
        ),
        check(
            f"{symbol}.close_vs_snapshot_tolerance",
            "passed" if not exceed else "warning",
            f"{exceed} of {comparison.comparable_count} close-basis vs official-snapshot "
            f"comparisons exceed {PUBLIC_SMOKE_RATE_TOLERANCE} (descriptive: different "
            "instants are compared; tolerance not relaxed)",
            category="descriptive",
        ),
    ]
    inputs = [dict(item, role=f"{symbol}.{item['role']}") for item in basis.inputs]
    return summary, checks, inputs


def public_smoke_builder(
    symbols: tuple[str, ...],
    clock: Callable[[], datetime],
    *,
    request_budget_per_symbol: int = PUBLIC_SMOKE_REQUEST_BUDGET_PER_SYMBOL,
):
    """Builder for `run_to_bundle`; `clock` is read once."""

    def build(bundle: OutputBundle):
        now = clock()
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=7)
        decimal_context = normalize_decimal_context(None)
        effective = {
            "symbols": list(symbols),
            "timeframe": _TIMEFRAME,
            "start": start,
            "end": end,
            "as_of": now,
            "official_contract_type": "PERPETUAL",
            "rate_tolerance": PUBLIC_SMOKE_RATE_TOLERANCE,
            "http_timeout_seconds": str(PUBLIC_SMOKE_TIMEOUT_SECONDS),
            "max_attempts": PUBLIC_SMOKE_MAX_ATTEMPTS,
            "request_budget_per_symbol": request_budget_per_symbol,
            "decimal_context": decimal_context,
        }
        section = Section(
            limitations=list(BASIS_LIMITATIONS)
            + [
                (
                    "official record at T is a snapshot at T; the derived close basis is the close "
                    "of [T-1h, T) — differences are expected, measured and reported as warnings"
                ),
                "the window depends on the run clock; as_of and window are recorded in config",
            ],
            does_not_prove=list(BASIS_DOES_NOT_PROVE),
        )
        results = {}
        with localcontext(build_context(decimal_context)):
            for symbol in symbols:
                try:
                    summary, checks, inputs = _run_symbol(
                        bundle, symbol, start, end, now, request_budget_per_symbol
                    )
                except Exception as exc:  # noqa: BLE001 - recorded per symbol, run fails
                    message = _sanitize(f"{type(exc).__name__}: {exc}")
                    results[symbol] = {"execution": "failed", "error": message}
                    section.errors.append(f"{symbol}: {message}")
                    section.checks.append(
                        check(f"{symbol}.execution", "failed", message, category="execution")
                    )
                    continue
                results[symbol] = {"execution": "succeeded", **summary}
                section.checks += checks
                section.inputs += inputs
        section.results = results
        return to_view(effective), sha256_hex(canonical_json(effective)), section

    return build
