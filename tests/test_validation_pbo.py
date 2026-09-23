import dataclasses
import decimal
import inspect
import typing
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.deflated_sharpe as deflated_sharpe_module
import crypto_quant_lab.validation.metrics as metrics_module
import crypto_quant_lab.validation.pbo as pbo_module
import crypto_quant_lab.validation.return_matrix as return_matrix_module
import crypto_quant_lab.validation.rolling as rolling_module
import crypto_quant_lab.validation.trial_group as trial_group_module
from crypto_quant_lab.backtest.costs import ZeroCostModel
from crypto_quant_lab.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PositionTarget,
)
from crypto_quant_lab.market_data.models import Candle
from crypto_quant_lab.storage.base import HistoricalCandle
from crypto_quant_lab.storage.sqlite import SQLiteHistoricalCandleStore
from crypto_quant_lab.validation.candidate import Candidate, Trial
from crypto_quant_lab.validation.metrics import compute_periodic_returns, compute_stage2_metrics
from crypto_quant_lab.validation.pbo import (
    CscvCombination,
    PboResult,
    compute_probability_of_backtest_overfitting,
)
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix, build_trial_return_matrix
from crypto_quant_lab.validation.rolling import run_rolling_backtest_from_store
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.windows import TemporalWindow

START = datetime(2024, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
EXCHANGE = "binance"
MARKET_TYPE = "usdm_perp"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
AS_OF_TIME = datetime(2024, 1, 3, tzinfo=UTC)

# ln values at 28 significant digits, cross-checked offline with mpmath (dps=50);
# independent of the module under test.
LN_2 = Decimal("0.6931471805599453094172321215")
LN_3 = Decimal("1.098612288668109691395245237")
LN_5_OVER_3 = Decimal("0.5108256237659906832055140963")
LOGIT_TOLERANCE = Decimal("1e-26")


def _matrix(columns):
    """Build a matrix from {candidate_id: [row values]}; every row in one window."""
    ids = tuple(columns)
    row_count = len(next(iter(columns.values())))
    return TrialReturnMatrix(
        candidate_ids=ids,
        observation_times=tuple(START + HOUR * (index + 1) for index in range(row_count)),
        window_indices=(0,) * row_count,
        returns=tuple(tuple(Decimal(columns[cid][row]) for cid in ids) for row in range(row_count)),
    )


def _pbo(matrix, **kwargs):
    kwargs.setdefault("block_count", 2)
    return compute_probability_of_backtest_overfitting(matrix, **kwargs)


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


def _close(value, expected):
    with localcontext(Context(prec=60)):
        return abs(value - expected) <= LOGIT_TOLERANCE


# Hand-traceable fixtures. Every two-row half has the same spread (0.02), so the
# per-observation Sharpe ratio orders candidates exactly by the half's row sum.
ANTI_PERSISTENT = {  # the IS winner is the OOS loser in both combinations
    "A": ["0.02", "0.04", "-0.03", "-0.01"],
    "B": ["0.01", "0.03", "0.00", "0.02"],
}
PERSISTENT = {  # A wins everywhere
    "A": ["0.02", "0.04", "0.02", "0.04"],
    "B": ["0.01", "0.03", "0.01", "0.03"],
}
MEDIAN = {
    # blocks 0 / 1 sums: A .06/.04, B .04/.00, C .00/.06
    "A": ["0.02", "0.04", "0.01", "0.03"],
    "B": ["0.01", "0.03", "-0.01", "0.01"],
    "C": ["-0.01", "0.01", "0.02", "0.04"],
}
IS_TIE = {
    # block 0: A and B tie for best IS; block 1: A best IS, and in block 0 A ties B OOS
    "A": ["0.02", "0.04", "0.02", "0.04"],
    "B": ["0.02", "0.04", "-0.01", "0.01"],
    "C": ["0.00", "0.02", "0.01", "0.03"],
}


# ================================================================
# API / scope
# ================================================================


def test_public_symbols_are_exactly_the_locked_api():
    public = {name for name in dir(pbo_module) if not name.startswith("_")}
    assert public == {"CscvCombination", "PboResult", "compute_probability_of_backtest_overfitting"}


def test_signature_and_result_shapes_are_locked():
    signature = inspect.signature(compute_probability_of_backtest_overfitting)
    assert list(signature.parameters) == ["matrix", "block_count", "risk_free_per_period"]
    assert signature.parameters["block_count"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["risk_free_per_period"].default == Decimal(0)
    assert typing.get_type_hints(compute_probability_of_backtest_overfitting)["return"] is PboResult
    assert [f.name for f in dataclasses.fields(PboResult)] == [
        "block_count",
        "combinations",
        "probability_of_backtest_overfitting",
    ]
    assert [f.name for f in dataclasses.fields(CscvCombination)] == [
        "in_sample_blocks",
        "selected_candidate_ids",
        "out_of_sample_ranks",
        "logits",
    ]
    result = _pbo(_matrix(PERSISTENT))
    with pytest.raises(FrozenInstanceError):
        result.block_count = 4
    assert not hasattr(result, "__dict__")


def test_not_exported_at_package_root():
    for name in ("CscvCombination", "PboResult", "compute_probability_of_backtest_overfitting"):
        assert not hasattr(validation_package, name)


def _import_lines(module):
    return [
        line.strip()
        for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]


def test_import_direction_and_forbidden_scope():
    modules = {line.split()[1] for line in _import_lines(pbo_module)}
    assert modules == {
        "dataclasses",
        "decimal",
        "fractions",
        "itertools",
        "math",
        "crypto_quant_lab.validation.return_matrix",
    }
    source = inspect.getsource(pbo_module)
    for forbidden in ("float(", "import random", "import time", "BacktestResult", "cpcv", "CPCV("):
        assert forbidden not in source
    names = {name.lower() for name in dir(pbo_module)}
    assert not ({"select_strategy", "order", "risk_profile", "sample", "random"} & names)


@pytest.mark.parametrize(
    "module",
    [
        return_matrix_module,
        metrics_module,
        deflated_sharpe_module,
        trial_group_module,
        rolling_module,
        validation_package,
    ],
    ids=lambda module: module.__name__,
)
def test_no_existing_module_imports_pbo(module):
    for line in _import_lines(module):
        assert ".pbo" not in line and "import pbo" not in line


# ================================================================
# known PBO results (hand-traced)
# ================================================================


def test_anti_persistent_selection_has_pbo_one():
    result = _pbo(_matrix(ANTI_PERSISTENT))
    assert result.probability_of_backtest_overfitting == Decimal(1)
    assert [c.in_sample_blocks for c in result.combinations] == [(0,), (1,)]
    assert [c.selected_candidate_ids for c in result.combinations] == [("A",), ("B",)]
    assert [c.out_of_sample_ranks for c in result.combinations] == [(Decimal(1),), (Decimal(1),)]
    # rank 1 of N=2: ω̄ = 1/3, λ = ln(1/2) = -ln 2
    for combination in result.combinations:
        assert _close(combination.logits[0], -LN_2)


def test_persistent_selection_has_pbo_zero():
    result = _pbo(_matrix(PERSISTENT))
    assert result.probability_of_backtest_overfitting == Decimal(0)
    assert [c.selected_candidate_ids for c in result.combinations] == [("A",), ("A",)]
    assert [c.out_of_sample_ranks for c in result.combinations] == [(Decimal(2),), (Decimal(2),)]
    for combination in result.combinations:
        assert _close(combination.logits[0], LN_2)


def test_exact_oos_median_counts_as_overfit():
    result = _pbo(_matrix(MEDIAN))
    first, second = result.combinations
    # combination (0,): A wins IS, OOS rank 2 of 3 -> ω̄ = 1/2, λ = 0 exactly
    assert first.selected_candidate_ids == ("A",)
    assert first.out_of_sample_ranks == (Decimal(2),)
    assert first.logits == (Decimal(0),)
    # combination (1,): C wins IS, OOS rank 1 of 3 -> λ = ln(1/3)
    assert second.selected_candidate_ids == ("C",)
    assert second.out_of_sample_ranks == (Decimal(1),)
    assert _close(second.logits[0], -LN_3)
    # λ <= 0 in both (Section 3.1: φ integrates f(λ) up to and including 0).
    # The strict r̄ < N/2 reading of Definition 2.2 would give 0.5 instead.
    assert result.probability_of_backtest_overfitting == Decimal(1)


def test_in_sample_tie_splits_the_combination_weight_across_tied_winners():
    result = _pbo(_matrix(IS_TIE))
    first, second = result.combinations
    assert first.selected_candidate_ids == ("A", "B")
    assert first.out_of_sample_ranks == (Decimal(3), Decimal(1))  # A not overfit, B overfit
    assert second.selected_candidate_ids == ("A",)
    # OOS in block 0: A ties B at the top -> average rank 2.5 (not overfit)
    assert second.out_of_sample_ranks == (Decimal("2.5"),)
    assert _close(second.logits[0], LN_5_OVER_3)
    # (1/2 + 0) / 2
    assert result.probability_of_backtest_overfitting == Decimal("0.25")


def test_column_order_does_not_change_pbo_or_tie_handling():
    reordered = {cid: IS_TIE[cid] for cid in ("C", "B", "A")}
    result = _pbo(_matrix(reordered))
    assert result.probability_of_backtest_overfitting == Decimal("0.25")
    # tied winners are reported in matrix column order
    assert result.combinations[0].selected_candidate_ids == ("B", "A")
    assert result.combinations[0].out_of_sample_ranks == (Decimal(1), Decimal(3))


def test_all_combinations_in_lexicographic_order_with_complements():
    columns = {
        "A": ["0.01", "0.03", "0.02", "0.05", "-0.01", "0.02", "0.04", "0.00"],
        "B": ["0.02", "0.00", "0.01", "0.03", "0.02", "-0.02", "0.01", "0.03"],
    }
    result = _pbo(_matrix(columns), block_count=4)
    blocks = [c.in_sample_blocks for c in result.combinations]
    assert blocks == [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    for combination in blocks:
        complement = tuple(b for b in range(4) if b not in combination)
        assert complement in blocks
    assert result.block_count == 4


def test_blocks_are_contiguous_in_original_row_order():
    # contiguous halves give PBO 1; an interleaved split (rows 0,2 / 1,3) would not
    result = _pbo(_matrix(ANTI_PERSISTENT))
    assert result.probability_of_backtest_overfitting == Decimal(1)
    interleaved = {
        cid: [values[0], values[2], values[1], values[3]] for cid, values in ANTI_PERSISTENT.items()
    }
    assert _pbo(_matrix(interleaved)).probability_of_backtest_overfitting != Decimal(1)


def test_single_row_blocks_with_s_equal_t():
    # every row value differs within a column and B = A - 0.01 row by row, so any
    # two-row half gives equal spreads and A always has the higher Sharpe ratio
    columns = {"A": ["0.02", "0.04", "0.03", "0.05"], "B": ["0.01", "0.03", "0.02", "0.04"]}
    result = _pbo(_matrix(columns), block_count=4)
    assert len(result.combinations) == 6
    assert result.probability_of_backtest_overfitting == Decimal(0)


# ================================================================
# subsample Sharpe equivalence with Stage-2 (private helper, justified)
# ================================================================


def _stage2_result(equities):
    points = tuple(
        EquityPoint(time=START + HOUR * (index + 1), equity=Decimal(value))
        for index, value in enumerate(equities)
    )
    return BacktestResult(
        initial_cash=Decimal(1000),
        final_cash=points[-1].equity,
        final_equity=points[-1].equity,
        total_realized_pnl=Decimal(0),
        total_unrealized_pnl=Decimal(0),
        total_pnl=Decimal(0),
        total_cost=Decimal(0),
        fill_count=0,
        trade_count=0,
        equity_curve=points,
    )


@pytest.mark.parametrize("rf", [Decimal(0), Decimal("0.0003")])
def test_subsample_sharpe_equals_stage2_sharpe_exactly(rf):
    result = _stage2_result(["1010", "1004", "1021", "1015", "1030", "1026", "1041"])
    returns = list(compute_periodic_returns(result))
    expected = compute_stage2_metrics(result, risk_free_per_period=rf).sharpe_ratio
    assert pbo_module._subsample_sharpe_ratio(returns, rf) == expected


def test_subsample_sharpe_returns_none_for_zero_stdev():
    assert pbo_module._subsample_sharpe_ratio([Decimal("0.01")] * 3, Decimal(0)) is None


# ================================================================
# undefined metric, invalid input, cost limits, validation order
# ================================================================


def test_zero_in_sample_stdev_is_an_error_not_zero_or_infinity():
    columns = {"A": ["0.01", "0.01", "0.02", "0.03"], "B": ["0.02", "0.03", "0.01", "0.04"]}
    _raises_exactly(
        ValueError,
        "in-sample Sharpe ratio is undefined for candidate 'A' in combination with "
        "in-sample blocks (0,): zero standard deviation",
        _pbo,
        _matrix(columns),
    )


def test_zero_out_of_sample_stdev_is_an_error():
    columns = {"A": ["0.02", "0.03", "0.01", "0.01"], "B": ["0.02", "0.04", "0.01", "0.04"]}
    _raises_exactly(
        ValueError,
        "out-of-sample Sharpe ratio is undefined for candidate 'A' in combination with "
        "in-sample blocks (0,): zero standard deviation",
        _pbo,
        _matrix(columns),
    )


def test_invalid_inputs_have_exact_messages():
    matrix = _matrix(PERSISTENT)
    cases = [
        ((), {}, TypeError, "matrix must be a TrialReturnMatrix, got tuple"),
        (matrix, {"block_count": True}, TypeError, "block_count must be an int, got bool"),
        (matrix, {"block_count": "2"}, TypeError, "block_count must be an int, got str"),
        (matrix, {"block_count": 0}, ValueError, "block_count must be an even integer >= 2, got 0"),
        (matrix, {"block_count": 3}, ValueError, "block_count must be an even integer >= 2, got 3"),
        (
            matrix,
            {"block_count": -2},
            ValueError,
            "block_count must be an even integer >= 2, got -2",
        ),
        (
            matrix,
            {"risk_free_per_period": 0.0},
            TypeError,
            "risk_free_per_period must be a Decimal, got float",
        ),
        (
            matrix,
            {"risk_free_per_period": Decimal("NaN")},
            ValueError,
            "risk_free_per_period must be finite, got NaN",
        ),
    ]
    for target, kwargs, error, message in cases:
        _raises_exactly(error, message, _pbo, target, **kwargs)


def test_single_candidate_is_rejected():
    _raises_exactly(
        ValueError,
        "at least two candidates are required to rank out-of-sample performance, got 1",
        _pbo,
        _matrix({"A": ["0.01", "0.02", "0.03", "0.05"]}),
    )


def test_rows_are_never_trimmed_or_padded():
    columns = {"A": ["0.01", "0.02", "0.03", "0.05", "0.01", "0.00"], "B": ["0"] * 6}
    _raises_exactly(
        ValueError,
        "row count 6 is not divisible by block_count 4; rows are never trimmed or padded",
        _pbo,
        _matrix(columns),
        block_count=4,
    )


def test_half_sample_needs_two_rows():
    _raises_exactly(
        ValueError,
        "each half-sample must contain at least two rows to compute a sample standard "
        "deviation, got 1",
        _pbo,
        _matrix({"A": ["0.01", "0.02"], "B": ["0.02", "0.01"]}),
    )


def test_cost_limit_rejects_without_sampling():
    columns = {"A": ["0.01"] * 24, "B": ["0.02"] * 24}  # constant: would fail if evaluated
    _raises_exactly(
        ValueError,
        "CSCV cost of 129799488 cell evaluations (C(24, 12)=2704156 x T=24 x N=2) "
        "exceeds the limit of 20000000; no sampling is performed",
        _pbo,
        _matrix(columns),
        block_count=24,
    )


def test_cost_limit_boundary_counts_rows_and_candidates(monkeypatch):
    matrix = _matrix(PERSISTENT)  # C(2,1)=2 x T=4 x N=2 = 16 cells
    monkeypatch.setattr(pbo_module, "_MAX_CELL_EVALUATIONS", 16)
    assert _pbo(matrix).probability_of_backtest_overfitting == Decimal(0)
    monkeypatch.setattr(pbo_module, "_MAX_CELL_EVALUATIONS", 15)
    _raises_exactly(
        ValueError,
        "CSCV cost of 16 cell evaluations (C(2, 1)=2 x T=4 x N=2) exceeds the limit of 15; "
        "no sampling is performed",
        _pbo,
        matrix,
    )


def test_validation_order():
    single = _matrix({"A": ["0.01", "0.02", "0.03"]})
    # matrix type wins over block_count
    _raises_exactly(
        TypeError, "matrix must be a TrialReturnMatrix, got NoneType", _pbo, None, block_count=3
    )
    # block_count type wins over rf
    _raises_exactly(
        TypeError,
        "block_count must be an int, got float",
        _pbo,
        single,
        block_count=2.0,
        risk_free_per_period=0.0,
    )
    # block_count value wins over rf
    _raises_exactly(
        ValueError,
        "block_count must be an even integer >= 2, got 3",
        _pbo,
        single,
        block_count=3,
        risk_free_per_period=0.0,
    )
    # rf wins over candidate count
    _raises_exactly(
        TypeError,
        "risk_free_per_period must be a Decimal, got float",
        _pbo,
        single,
        risk_free_per_period=0.0,
    )
    # candidate count wins over divisibility (3 rows, S=2)
    _raises_exactly(
        ValueError,
        "at least two candidates are required to rank out-of-sample performance, got 1",
        _pbo,
        single,
    )
    # divisibility wins over half-sample size (3 rows, S=2)
    _raises_exactly(
        ValueError,
        "row count 3 is not divisible by block_count 2; rows are never trimmed or padded",
        _pbo,
        _matrix({"A": ["0.01", "0.02", "0.03"], "B": ["0.01", "0.02", "0.03"]}),
    )
    # cost wins over undefined Sharpe (checked in test_cost_limit_rejects_without_sampling)


# ================================================================
# determinism, context independence, immutability, risk-free rate
# ================================================================


def test_determinism_and_input_immutability():
    matrix = _matrix(IS_TIE)
    snapshot = hash(matrix)
    first = _pbo(matrix)
    second = _pbo(matrix)
    assert first == second
    assert hash(first) == hash(second)
    assert hash(matrix) == snapshot


def test_ambient_decimal_context_does_not_change_result():
    matrix = _matrix(MEDIAN)
    baseline = _pbo(matrix)
    hostile = Context(
        prec=2,
        rounding=decimal.ROUND_DOWN,
        traps=[decimal.Inexact, decimal.Rounded, decimal.DivisionByZero],
    )
    with localcontext(hostile):
        assert _pbo(matrix) == baseline


def test_risk_free_rate_can_change_the_in_sample_winner():
    # A: higher mean, higher spread; B: lower mean, lower spread
    columns = {
        "A": ["0.00", "0.06", "0.00", "0.06"],
        "B": ["0.01", "0.02", "0.01", "0.02"],
    }
    matrix = _matrix(columns)
    assert _pbo(matrix).combinations[0].selected_candidate_ids == ("B",)
    shifted = _pbo(matrix, risk_free_per_period=Decimal("0.02"))
    assert shifted.combinations[0].selected_candidate_ids == ("A",)


def test_result_model_validates_its_own_fields():
    _raises_exactly(
        ValueError,
        "selected_candidate_ids must not be empty",
        CscvCombination,
        in_sample_blocks=(0,),
        selected_candidate_ids=(),
        out_of_sample_ranks=(),
        logits=(),
    )
    _raises_exactly(
        ValueError,
        "probability_of_backtest_overfitting must be within [0, 1], got 1.5",
        PboResult,
        block_count=2,
        combinations=(),
        probability_of_backtest_overfitting=Decimal("1.5"),
    )


# ================================================================
# real rolling -> TrialGroup -> TrialReturnMatrix -> PBO integration
# ================================================================

PRICES = (
    "100", "103", "99", "104", "101", "107", "102", "108", "104", "110",
    "105", "111", "106", "113", "109", "115",
)  # fmt: skip


class _LongPolicy:
    def target_position(self, context):
        return PositionTarget.LONG


class _ShortPolicy:
    def target_position(self, context):
        return PositionTarget.SHORT


class _AlternatingPolicy:
    def target_position(self, context):
        return PositionTarget.LONG if len(context.candles) % 2 else PositionTarget.SHORT


POLICIES = (("long", _LongPolicy), ("short", _ShortPolicy), ("alternating", _AlternatingPolicy))


def _candle(hour, price):
    candle = Candle(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        open_time=START + HOUR * hour,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal(1),
    )
    return HistoricalCandle(exchange=EXCHANGE, market_type=MARKET_TYPE, candle=candle)


def _rolling_group(store, windows, policies):
    trials = []
    for candidate_id, policy in policies:
        results = run_rolling_backtest_from_store(
            store,
            windows,
            policy_factory=policy,
            exchange=EXCHANGE,
            market_type=MARKET_TYPE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of_time=AS_OF_TIME,
            config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
            cost_model=ZeroCostModel(),
        )
        trials.append(
            Trial(
                candidate=Candidate(candidate_id=candidate_id, parameters=()),
                results=results,
                exchange=EXCHANGE,
                market_type=MARKET_TYPE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                as_of_time=AS_OF_TIME,
                config=BacktestConfig(initial_cash=Decimal(1000), position_quantity=Decimal(1)),
            )
        )
    return TrialGroup(group_id="rolling", trials=tuple(trials))


def test_real_rolling_trial_group_matrix_pbo_integration(tmp_path):
    store = SQLiteHistoricalCandleStore(tmp_path / "candles.db")
    try:
        store.write_batch([_candle(hour, price) for hour, price in enumerate(PRICES)])
        windows = (
            TemporalWindow(start=START, end=START + HOUR * 6),
            TemporalWindow(start=START + HOUR * 8, end=START + HOUR * 14),
        )
        matrix = build_trial_return_matrix(_rolling_group(store, windows, POLICIES))
        assert len(matrix.returns) == 12

        result = compute_probability_of_backtest_overfitting(matrix, block_count=4)
        assert len(result.combinations) == 6
        assert Decimal(0) <= result.probability_of_backtest_overfitting <= Decimal(1)
        for combination in result.combinations:
            assert set(combination.selected_candidate_ids) <= set(matrix.candidate_ids)

        # candidate order in the group does not change the estimate
        reordered = build_trial_return_matrix(
            _rolling_group(store, windows, tuple(reversed(POLICIES)))
        )
        assert (
            compute_probability_of_backtest_overfitting(
                reordered, block_count=4
            ).probability_of_backtest_overfitting
            == result.probability_of_backtest_overfitting
        )
        # determinism on real data
        assert compute_probability_of_backtest_overfitting(matrix, block_count=4) == result
    finally:
        store.close()
