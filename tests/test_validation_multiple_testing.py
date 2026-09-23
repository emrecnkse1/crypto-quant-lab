import dataclasses
import decimal
import inspect
import itertools
import typing
from dataclasses import FrozenInstanceError
from decimal import Context, Decimal, localcontext

import pytest

import crypto_quant_lab.validation as validation_package
import crypto_quant_lab.validation.deflated_sharpe as deflated_sharpe_module
import crypto_quant_lab.validation.multiple_testing as multiple_testing_module
import crypto_quant_lab.validation.pbo as pbo_module
import crypto_quant_lab.validation.trial_group as trial_group_module
from crypto_quant_lab.validation.multiple_testing import (
    HolmAdjustedHypothesis,
    HolmCorrectionResult,
    HypothesisPValue,
    apply_holm_correction,
)

ALPHA = Decimal("0.05")


def _family(pairs):
    return tuple(HypothesisPValue(hypothesis_id=hid, p_value=Decimal(p)) for hid, p in pairs)


def _holm(pairs, *, family_id="fam", alpha=ALPHA):
    return apply_holm_correction(family_id, _family(pairs), significance_level=alpha)


def _by_id(result):
    return {h.hypothesis_id: (h.adjusted_p_value, h.rejected) for h in result.hypotheses}


def _raises_exactly(exception_type, message, callable_, *args, **kwargs):
    with pytest.raises(exception_type) as excinfo:
        callable_(*args, **kwargs)
    assert type(excinfo.value) is exception_type
    assert str(excinfo.value) == message


# Reference values below are derived BY HAND from Holm's definition as coded in
# R's stats::p.adjust ("holm": pmin(1, cummax((n + 1 - i) * p[o]))[ro]); they are
# exact rationals, written literally, and are not produced by the module.


# ================================================================
# API / scope
# ================================================================


def test_public_symbols_are_exactly_the_locked_api():
    public = {name for name in dir(multiple_testing_module) if not name.startswith("_")}
    assert public == {
        "HypothesisPValue",
        "HolmAdjustedHypothesis",
        "HolmCorrectionResult",
        "apply_holm_correction",
    }


def test_signature_and_model_shapes_are_locked():
    signature = inspect.signature(apply_holm_correction)
    assert list(signature.parameters) == ["family_id", "hypotheses", "significance_level"]
    assert signature.parameters["significance_level"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["significance_level"].default is inspect.Parameter.empty
    assert typing.get_type_hints(apply_holm_correction)["return"] is HolmCorrectionResult
    assert [f.name for f in dataclasses.fields(HypothesisPValue)] == ["hypothesis_id", "p_value"]
    assert [f.name for f in dataclasses.fields(HolmAdjustedHypothesis)] == [
        "hypothesis_id",
        "p_value",
        "adjusted_p_value",
        "rejected",
    ]
    assert [f.name for f in dataclasses.fields(HolmCorrectionResult)] == [
        "family_id",
        "significance_level",
        "hypotheses",
    ]
    result = _holm([("a", "0.01")])
    with pytest.raises(FrozenInstanceError):
        result.family_id = "other"
    assert not hasattr(result, "__dict__")


def test_not_exported_at_package_root():
    for name in (
        "HypothesisPValue",
        "HolmAdjustedHypothesis",
        "HolmCorrectionResult",
        "apply_holm_correction",
    ):
        assert not hasattr(validation_package, name)


def _import_lines(module):
    return [
        line.strip()
        for line in inspect.getsource(module).splitlines()
        if line.strip().startswith(("import ", "from "))
    ]


def test_module_is_standalone_and_links_to_no_trial_group_dsr_or_pbo():
    modules = {line.split()[1] for line in _import_lines(multiple_testing_module)}
    assert modules == {"dataclasses", "decimal"}
    source = inspect.getsource(multiple_testing_module)
    for forbidden in ("recorded_trial_count", "TrialGroup", "float(", "localcontext", "getcontext"):
        assert forbidden not in source
    for module in (trial_group_module, deflated_sharpe_module, pbo_module, validation_package):
        for line in _import_lines(module):
            assert "multiple_testing" not in line


# ================================================================
# known Holm results (hand-derived)
# ================================================================


def test_known_adjusted_p_values_and_decisions():
    # sorted: d .005 x4 = .02, a .01 x3 = .03, c .03 x2 = .06, b .04 x1 = .04 -> cummax .06
    result = _holm([("a", "0.01"), ("b", "0.04"), ("c", "0.03"), ("d", "0.005")])
    assert _by_id(result) == {
        "a": (Decimal("0.03"), True),
        "b": (Decimal("0.06"), False),
        "c": (Decimal("0.06"), False),
        "d": (Decimal("0.020"), True),
    }


def test_no_rejection_after_the_first_non_rejected_hypothesis():
    # x .01 x3 = .03 (reject); y .04 x2 = .08 (stop); z .045 x1 = .045 -> cummax .08.
    # z's own p (.045) is below alpha/1 but Holm must not reject it after y.
    result = _holm([("x", "0.01"), ("y", "0.04"), ("z", "0.045")])
    assert _by_id(result) == {
        "x": (Decimal("0.03"), True),
        "y": (Decimal("0.08"), False),
        "z": (Decimal("0.08"), False),
    }


def test_adjusted_p_values_are_capped_at_one():
    # .6 x2 = 1.2 -> 1; .7 x1 = .7 -> cummax 1.2 -> 1
    result = _holm([("a", "0.6"), ("b", "0.7")])
    assert _by_id(result) == {"a": (Decimal(1), False), "b": (Decimal(1), False)}


def test_adjusted_p_values_are_monotone_in_the_raw_p_values():
    result = _holm([("a", "0.2"), ("b", "0.001"), ("c", "0.03"), ("d", "0.03"), ("e", "0.9")])
    pairs = sorted((h.p_value, h.adjusted_p_value) for h in result.hypotheses)
    adjusted = [pair[1] for pair in pairs]
    assert adjusted == sorted(adjusted)


def test_decisions_match_the_step_down_procedure():
    # step-down: p_(i) <= alpha / (m - i + 1) until the first failure
    raw = [("a", "0.004"), ("b", "0.011"), ("c", "0.02"), ("d", "0.013"), ("e", "0.5")]
    # m = 5: .004 <= .01 yes; .011 <= .0125 yes; .013 <= .016666.. yes; .02 <= .025 yes;
    # .5 <= .05 no
    result = _holm(raw)
    assert {h.hypothesis_id for h in result.hypotheses if h.rejected} == {"a", "b", "c", "d"}


# ================================================================
# ties, input order, identity mapping
# ================================================================


def test_tied_p_values_receive_equal_adjusted_values():
    # .02 x3 = .06, .02 x2 = .04 -> cummax .06; .5 x1 -> .5
    result = _holm([("a", "0.02"), ("b", "0.02"), ("c", "0.5")])
    assert _by_id(result) == {
        "a": (Decimal("0.06"), False),
        "b": (Decimal("0.06"), False),
        "c": (Decimal("0.5"), False),
    }


def test_input_order_does_not_change_any_hypothesis_result():
    raw = [("a", "0.02"), ("b", "0.02"), ("c", "0.5"), ("d", "0.001"), ("e", "0.02")]
    reference = _by_id(_holm(raw))
    for permutation in itertools.permutations(raw):
        result = _holm(list(permutation))
        assert [h.hypothesis_id for h in result.hypotheses] == [hid for hid, _ in permutation]
        assert _by_id(result) == reference


def test_output_preserves_input_order_ids_raw_p_values_and_family():
    family = _family([("h2", "0.3"), ("h1", "0.001"), ("h3", "0.04")])
    result = apply_holm_correction("family-2024", family, significance_level=ALPHA)
    assert result.family_id == "family-2024"
    assert result.significance_level is ALPHA
    assert [h.hypothesis_id for h in result.hypotheses] == ["h2", "h1", "h3"]
    for given, returned in zip(family, result.hypotheses, strict=True):
        assert returned.p_value is given.p_value


# ================================================================
# single hypothesis, boundaries, exactness
# ================================================================


def test_single_hypothesis_is_unadjusted():
    result = _holm([("only", "0.037")])
    assert _by_id(result) == {"only": (Decimal("0.037"), True)}


def test_p_values_zero_and_one():
    result = _holm([("zero", "0"), ("one", "1")])
    # 0 x2 = 0; 1 x1 = 1
    assert _by_id(result) == {"zero": (Decimal(0), True), "one": (Decimal(1), False)}


def test_equality_with_the_significance_level_is_a_rejection():
    # single: adjusted .05 == alpha -> rejected
    assert _by_id(_holm([("a", "0.05")])) == {"a": (Decimal("0.05"), True)}
    # .0125 x4 = .05 exactly
    result = _holm([("a", "0.0125"), ("b", "0.5"), ("c", "0.6"), ("d", "0.7")])
    assert _by_id(result)["a"] == (Decimal("0.05"), True)
    # .0125001 x4 = .0500004, just above alpha -> not rejected
    above = _holm([("a", "0.0125001"), ("b", "0.5"), ("c", "0.6"), ("d", "0.7")])
    assert _by_id(above)["a"] == (Decimal("0.0500004"), False)


def test_arithmetic_is_exact_beyond_28_digits():
    third = "0.0333333333333333333333333333333333"  # 34 significant digits
    result = _holm([("a", third), ("b", "0.9"), ("c", "0.95")])
    assert _by_id(result)["a"][0] == Decimal("0.0999999999999999999999999999999999")


def test_ambient_decimal_context_does_not_change_results():
    raw = [("a", "0.0333333333333333333333333333333333"), ("b", "0.0125"), ("c", "0.9")]
    baseline = _holm(raw)
    hostile = Context(
        prec=2,
        rounding=decimal.ROUND_DOWN,
        traps=[decimal.Inexact, decimal.Rounded, decimal.InvalidOperation],
    )
    with localcontext(hostile):
        assert _holm(raw) == baseline


def test_inputs_are_not_mutated_and_results_are_deterministic():
    family = _family([("a", "0.01"), ("b", "0.2")])
    snapshot = (family, hash(family))
    first = apply_holm_correction("fam", family, significance_level=ALPHA)
    second = apply_holm_correction("fam", family, significance_level=ALPHA)
    assert first == second
    assert (family, hash(family)) == snapshot


def test_negative_zero_p_value_is_normalized_to_zero():
    result = _holm([("a", "-0"), ("b", "0.5")])
    adjusted = result.hypotheses[0].adjusted_p_value
    assert adjusted == Decimal(0)
    assert not adjusted.is_signed()


# ================================================================
# invalid inputs and validation order
# ================================================================


@pytest.mark.parametrize(
    ("hypothesis_id", "error", "message"),
    [
        (1, TypeError, "hypothesis_id must be a str, got int"),
        ("", ValueError, "hypothesis_id must not be empty or whitespace-only"),
        ("  ", ValueError, "hypothesis_id must not be empty or whitespace-only"),
        (" h", ValueError, "hypothesis_id must not have leading/trailing whitespace padding"),
    ],
)
def test_invalid_hypothesis_ids(hypothesis_id, error, message):
    _raises_exactly(error, message, HypothesisPValue, hypothesis_id=hypothesis_id, p_value=ALPHA)


@pytest.mark.parametrize(
    ("p_value", "error", "message"),
    [
        (0.01, TypeError, "p_value must be a Decimal, got float"),
        (0, TypeError, "p_value must be a Decimal, got int"),
        (True, TypeError, "p_value must be a Decimal, got bool"),
        ("0.01", TypeError, "p_value must be a Decimal, got str"),
        (Decimal("NaN"), ValueError, "p_value must be finite and within [0, 1], got NaN"),
        (Decimal("sNaN"), ValueError, "p_value must be finite and within [0, 1], got sNaN"),
        (
            Decimal("Infinity"),
            ValueError,
            "p_value must be finite and within [0, 1], got Infinity",
        ),
        (Decimal("-0.001"), ValueError, "p_value must be finite and within [0, 1], got -0.001"),
        (Decimal("1.0001"), ValueError, "p_value must be finite and within [0, 1], got 1.0001"),
    ],
)
def test_invalid_p_values(p_value, error, message):
    _raises_exactly(error, message, HypothesisPValue, hypothesis_id="h", p_value=p_value)


def test_invalid_family_inputs_have_exact_messages():
    good = _family([("a", "0.01")])
    cases = [
        ((5, good), {}, TypeError, "family_id must be a str, got int"),
        (("", good), {}, ValueError, "family_id must not be empty or whitespace-only"),
        (
            ("fam ", good),
            {},
            ValueError,
            "family_id must not have leading/trailing whitespace padding",
        ),
        (("fam", list(good)), {}, TypeError, "hypotheses must be a tuple, got list"),
        (("fam", ()), {}, ValueError, "hypotheses must not be empty"),
        (
            ("fam", (good[0], ("b", "0.2"))),
            {},
            TypeError,
            "hypotheses[1] must be a HypothesisPValue, got tuple",
        ),
        (
            ("fam", _family([("a", "0.01"), ("b", "0.2"), ("a", "0.3")])),
            {},
            ValueError,
            "hypotheses[2].hypothesis_id 'a' duplicates hypotheses[0].hypothesis_id",
        ),
        (("fam", good), {"significance_level": 0.05}, TypeError,
         "significance_level must be a Decimal, got float"),
    ]  # fmt: skip
    for args, overrides, error, message in cases:
        kwargs = {"significance_level": ALPHA}
        kwargs.update(overrides)
        _raises_exactly(error, message, apply_holm_correction, *args, **kwargs)


@pytest.mark.parametrize("alpha", ["0", "1", "-0.1", "1.5", "NaN", "Infinity"])
def test_significance_level_must_be_strictly_between_zero_and_one(alpha):
    _raises_exactly(
        ValueError,
        f"significance_level must be finite and satisfy 0 < significance_level < 1, "
        f"got {Decimal(alpha)}",
        apply_holm_correction,
        "fam",
        _family([("a", "0.01")]),
        significance_level=Decimal(alpha),
    )


def test_validation_order():
    duplicate = _family([("a", "0.01"), ("a", "0.02")])
    # family_id wins over every hypotheses/alpha error
    _raises_exactly(
        TypeError,
        "family_id must be a str, got NoneType",
        apply_holm_correction,
        None,
        [],
        significance_level=0.05,
    )
    # hypotheses type wins over alpha
    _raises_exactly(
        ValueError, "hypotheses must not be empty", apply_holm_correction, "fam", (),
        significance_level=0.05,
    )  # fmt: skip
    # element type (later index) wins over an earlier duplicate
    _raises_exactly(
        TypeError,
        "hypotheses[2] must be a HypothesisPValue, got str",
        apply_holm_correction,
        "fam",
        (*duplicate, "x"),
        significance_level=ALPHA,
    )
    # duplicate wins over alpha
    _raises_exactly(
        ValueError,
        "hypotheses[1].hypothesis_id 'a' duplicates hypotheses[0].hypothesis_id",
        apply_holm_correction,
        "fam",
        duplicate,
        significance_level=Decimal(2),
    )


def test_result_models_validate_their_fields():
    _raises_exactly(
        ValueError,
        "adjusted_p_value must be finite and within [0, 1], got 1.2",
        HolmAdjustedHypothesis,
        hypothesis_id="h",
        p_value=Decimal("0.6"),
        adjusted_p_value=Decimal("1.2"),
        rejected=False,
    )
    _raises_exactly(
        TypeError,
        "rejected must be a bool, got int",
        HolmAdjustedHypothesis,
        hypothesis_id="h",
        p_value=Decimal("0.6"),
        adjusted_p_value=Decimal("0.6"),
        rejected=0,
    )
    _raises_exactly(
        TypeError,
        "hypotheses[0] must be a HolmAdjustedHypothesis, got str",
        HolmCorrectionResult,
        family_id="fam",
        significance_level=ALPHA,
        hypotheses=("x",),
    )
