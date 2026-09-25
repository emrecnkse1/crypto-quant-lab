"""Independent FAZ6C acceptance audit (VALIDATION_SPEC.md Bölüm 22.4, 28.Z).

Every expectation comes from a mathematical invariant or an independent
Fraction/Decimal re-computation, never from the module under test.
"""

from datetime import timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from itertools import combinations

import test_backtest_position_log as pl
import test_validation_cpcv_study as study_tests
import test_validation_deflated_sharpe as dsr_tests
import test_validation_pbo as base

from crypto_quant_lab.backtest.position_log import PositionIntervalRecorder
from crypto_quant_lab.validation.cpcv_study import run_cpcv_study
from crypto_quant_lab.validation.pbo_diagnostics import compute_pbo_diagnostics
from crypto_quant_lab.validation.return_matrix import TrialReturnMatrix
from crypto_quant_lab.validation.rolling import WindowResult
from crypto_quant_lab.validation.sharpe_significance import compute_hac_sharpe_significance
from crypto_quant_lab.validation.trial_group import TrialGroup
from crypto_quant_lab.validation.trial_statistics import (
    compute_pooled_deflated_sharpe_ratio,
    estimate_effective_trial_count,
)
from crypto_quant_lab.validation.windows import TemporalWindow

TOL = Decimal("1e-24")
A = dsr_tests.SYNTHETIC_CURVES
B = {c: tuple(reversed(v)) for c, v in A.items()}


def ref_sharpe(values):
    """Independent sample Sharpe (n - 1) from exact Fractions."""
    f = [Fraction(v) for v in values]
    m = sum(f) / len(f)
    var = sum((x - m) ** 2 for x in f) / (len(f) - 1)
    with localcontext(Context(prec=60)):
        return (Decimal(m.numerator) / Decimal(m.denominator)) / (
            Decimal(var.numerator) / Decimal(var.denominator)
        ).sqrt()


def two_window_group(first, second):
    w1 = TemporalWindow(start=dsr_tests.START, end=dsr_tests.START + timedelta(hours=8))
    w2 = TemporalWindow(start=w1.end, end=w1.end + timedelta(hours=8))
    return TrialGroup(group_id="g", trials=tuple(
        dsr_tests._trial(c, (WindowResult(window=w1, result=dsr_tests._result(first[c])),
                             WindowResult(window=w2, result=dsr_tests._result(second[c]))))
        for c in first))  # fmt: skip


def matrix(cols):
    ids = tuple(cols)
    n = len(next(iter(cols.values())))
    return TrialReturnMatrix(
        candidate_ids=ids,
        observation_times=tuple(base.START + base.HOUR * (t + 1) for t in range(n)),
        window_indices=(0,) * n,
        returns=tuple(tuple(Decimal(cols[c][t]) for c in ids) for t in range(n)),
    )


# ================================================================ position observer


def test_reversal_mark_carries_both_positions_on_a_real_replay():
    recorder = PositionIntervalRecorder()
    result = pl.run_backtest_replay(pl.CANDLES, as_of_time=base.AS_OF_TIME, config=pl.CONFIG,
                                    policy=pl.Script([pl.L, pl.SH, pl.SH, pl.SH, pl.SH, pl.SH]),
                                    cost_model=pl.ZeroCostModel(), position_observer=recorder)  # fmt: skip
    old, new = recorder.intervals
    assert (old.side, new.side, old.exit_time) == ("LONG", "SHORT", new.entry_time)
    marks = [p.time for p in result.equity_curve]
    equity = {p.time: p.equity for p in result.equity_curve}
    after = next(m for m in marks if m > old.exit_time)
    # LONG bought at open 103, reversed at open 99 (-4); SHORT from 99 marked at close 99 (0)
    assert equity[after] - equity[old.exit_time] == Decimal(-4)
    assert after in old.return_marks(marks) and after in new.return_marks(marks)


def test_context_candles_never_produce_intervals(tmp_path):
    from crypto_quant_lab.validation.rolling import (
        ContextAwareWindow,
        run_context_aware_rolling_backtest_with_positions_from_store,
    )

    store = base.SQLiteHistoricalCandleStore(tmp_path / "c.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        window = ContextAwareWindow(context_start=base.START,
                                    evaluation=TemporalWindow(start=base.START + base.HOUR * 4,
                                                              end=base.START + base.HOUR * 12))  # fmt: skip
        results, intervals = run_context_aware_rolling_backtest_with_positions_from_store(
            store, (window,), policy_factory=base.POLICIES[2][1], exchange=base.EXCHANGE,
            market_type=base.MARKET_TYPE, symbol=base.SYMBOL, timeframe=base.TIMEFRAME,
            as_of_time=base.AS_OF_TIME, config=pl.CONFIG, cost_model=pl.ZeroCostModel(),
        )  # fmt: skip
        marks = [p.time for p in results[0].result.equity_curve]
        assert intervals[0] and all(i.entry_time >= marks[0] for i in intervals[0])
    finally:
        store.close()


# ================================================================ CPCV: no leakage across candidates


def test_every_candidate_is_selected_on_the_same_independently_purged_rows(tmp_path):
    store = base.SQLiteHistoricalCandleStore(tmp_path / "c.db")
    try:
        store.write_batch([base._candle(h, p) for h, p in enumerate(base.PRICES)])
        group, intervals = study_tests.positions_group(store, study_tests.FOUR)
        study = run_cpcv_study(group, test_group_count=2, fold_groups=study_tests.HALVES,
                               lookback_windows=study_tests.zero_context(study_tests.FOUR),
                               position_intervals=intervals)  # fmt: skip
        m = study.matrix
        owners = study.result.row_groups
        for split, result in zip(study.fold_model.splits, study.result.splits, strict=True):
            purged = {  # re-derived independently of the module
                row
                for row in range(len(owners))
                if owners[row] in split.train_groups
                and _row_touched(m, study.fold_model, intervals, split, row)
            }
            train = [r for r in range(len(owners)) if owners[r] in split.train_groups
                     and r not in purged]  # fmt: skip
            assert result.train_row_count == len(train)
            for column, cid in enumerate(m.candidate_ids):  # the same rows for every candidate
                expected = ref_sharpe([m.returns[r][column] for r in train])
                assert abs(result.train_sharpe_ratios[column] - expected) <= TOL, (cid, split)
    finally:
        store.close()


def _row_touched(m, model, intervals, split, row):
    block = m.window_indices[row]
    marks = [m.observation_times[r] for r in range(len(m.window_indices))
             if m.window_indices[r] == block]  # fmt: skip
    owner = {t: next(g for g, w in enumerate(model.groups) if w.start < t <= w.end) for t in marks}
    for per_window in intervals.values():
        for interval in per_window[block]:
            after = None if interval.exit_time is None else next(
                (t for t in marks if t > interval.exit_time), None)  # fmt: skip
            held = [t for t in marks if interval.entry_time < t and (after is None or t <= after)]
            if m.observation_times[row] in held and any(owner[t] in split.test_groups
                                                        for t in held):  # fmt: skip
                return True
    return False


# ================================================================ HAC, pooled DSR, effective N, PBO


def _curve_from_returns(returns):
    with localcontext(Context(prec=80)):
        equity, out = Decimal(1000), []
        for r in returns:
            equity *= 1 + r
            out.append(str(equity))
    return tuple(out)


def test_hac_is_invariant_to_window_order_and_return_scale():
    z1 = [r.z_statistic for r in compute_hac_sharpe_significance(two_window_group(A, B), lag=1)]
    z2 = [r.z_statistic for r in compute_hac_sharpe_significance(two_window_group(B, A), lag=1)]
    assert z1 == z2  # within-window pairs and the pooled mean do not depend on window order
    # multiplying every return by 2 leaves SR = mu / sigma and V (hence z) unchanged
    returns = [Decimal(v) for v in ("0.01", "-0.02", "0.03", "0.00", "0.02", "0.01", "-0.01",
                                    "0.02", "0.015", "-0.005")]  # fmt: skip
    window = TemporalWindow(start=dsr_tests.START, end=dsr_tests.START + timedelta(hours=10))
    single = compute_hac_sharpe_significance(dsr_tests._group(
        {"x": _curve_from_returns(returns)}, window=window), lag=1)  # fmt: skip
    doubled = compute_hac_sharpe_significance(dsr_tests._group(
        {"x": _curve_from_returns([2 * r for r in returns])}, window=window), lag=1)  # fmt: skip
    assert single[0].status == doubled[0].status == "evaluated"
    assert abs(single[0].z_statistic - doubled[0].z_statistic) <= Decimal("1e-20")
    assert abs(single[0].variance - doubled[0].variance) <= Decimal("1e-20")


def test_pooled_dsr_is_invariant_to_window_order():
    kwargs = {"selected_candidate_id": "alpha", "independent_trial_count": 5}
    assert compute_pooled_deflated_sharpe_ratio(two_window_group(A, B), **kwargs) == (
        compute_pooled_deflated_sharpe_ratio(two_window_group(B, A), **kwargs)
    )


def test_effective_n_is_invariant_to_affine_column_changes_and_column_order():
    cols = {"a": ("0.01", "-0.02", "0.03", "0.00", "0.02"),
            "b": ("0.02", "0.01", "-0.01", "0.03", "0.00"),
            "c": ("-0.01", "0.02", "0.02", "-0.02", "0.01")}  # fmt: skip
    base_n = estimate_effective_trial_count(matrix(cols)).effective_trial_count
    scaled = dict(cols, b=tuple(str(Decimal(v) * 3 + Decimal("0.5")) for v in cols["b"]))
    assert abs(estimate_effective_trial_count(matrix(scaled)).effective_trial_count - base_n) <= TOL
    reordered = {k: cols[k] for k in ("c", "a", "b")}
    assert (
        abs(estimate_effective_trial_count(matrix(reordered)).effective_trial_count - base_n) <= TOL
    )


def test_pbo_diagnostic_points_are_the_independent_argmax_sharpes():
    cols = {"a": ("0.01", "-0.02", "0.03", "0.00", "0.02", "0.01", "-0.01", "0.02"),
            "b": ("0.02", "0.01", "-0.01", "0.03", "0.00", "-0.02", "0.01", "0.01"),
            "c": ("-0.01", "0.02", "0.02", "-0.02", "0.01", "0.03", "0.02", "-0.01")}  # fmt: skip
    diag = compute_pbo_diagnostics(matrix(cols), block_count=4)
    expected, losses = [], Fraction(0)
    for in_blocks in combinations(range(4), 2):
        rows_in = [r for b in in_blocks for r in (2 * b, 2 * b + 1)]
        rows_out = [r for b in range(4) if b not in in_blocks for r in (2 * b, 2 * b + 1)]
        is_s = {c: ref_sharpe([cols[c][r] for r in rows_in]) for c in cols}
        winners = [c for c in cols if abs(is_s[c] - max(is_s.values())) < Decimal("1e-20")]
        for c in winners:
            oos = ref_sharpe([cols[c][r] for r in rows_out])
            expected.append((is_s[c], oos))
            losses += Fraction(1, len(winners)) * (oos < 0)
    assert len(diag.points) == len(expected)
    for (x, y, _), (ex, ey) in zip(diag.points, expected, strict=True):
        assert abs(x - ex) <= TOL and abs(y - ey) <= TOL
    assert diag.probability_of_loss == Decimal(losses.numerator) / Decimal(losses.denominator)
