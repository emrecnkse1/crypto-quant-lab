import pytest

from crypto_quant_lab.data_quality.retry import with_connection_retry
from crypto_quant_lab.storage.base import DataConflictError, DataCorruptionError, StorageError


def _scripted_operation(*outcomes):
    calls = []
    outcomes_iter = iter(outcomes)

    def _operation(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        outcome = next(outcomes_iter)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    _operation.calls = calls
    return _operation


def test_first_attempt_success_makes_exactly_one_call():
    op = _scripted_operation("ok")
    wrapped = with_connection_retry(op)

    result = wrapped()

    assert result == "ok"
    assert len(op.calls) == 1


def test_one_connection_error_then_success_retries_once():
    op = _scripted_operation(ConnectionError("first"), "ok")
    wrapped = with_connection_retry(op)

    result = wrapped()

    assert result == "ok"
    assert len(op.calls) == 2


def test_two_connection_errors_then_success_uses_all_attempts():
    op = _scripted_operation(ConnectionError("e1"), ConnectionError("e2"), "ok")
    wrapped = with_connection_retry(op, max_attempts=3)

    result = wrapped()

    assert result == "ok"
    assert len(op.calls) == 3


def test_connection_error_every_attempt_propagates_after_max_attempts():
    op = _scripted_operation(ConnectionError("e1"), ConnectionError("e2"), ConnectionError("e3"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(ConnectionError, match="e3"):
        wrapped()

    assert len(op.calls) == 3


def test_max_attempts_one_makes_single_call_and_propagates():
    op = _scripted_operation(ConnectionError("only"))
    wrapped = with_connection_retry(op, max_attempts=1)

    with pytest.raises(ConnectionError, match="only"):
        wrapped()

    assert len(op.calls) == 1


def test_value_error_is_not_retried():
    op = _scripted_operation(ValueError("bad"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(ValueError, match="bad"):
        wrapped()

    assert len(op.calls) == 1


def test_type_error_is_not_retried():
    op = _scripted_operation(TypeError("bad type"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(TypeError, match="bad type"):
        wrapped()

    assert len(op.calls) == 1


def test_storage_error_is_not_retried():
    op = _scripted_operation(StorageError("storage broke"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(StorageError, match="storage broke"):
        wrapped()

    assert len(op.calls) == 1


def test_data_conflict_error_is_not_retried():
    op = _scripted_operation(DataConflictError("conflict"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(DataConflictError, match="conflict"):
        wrapped()

    assert len(op.calls) == 1


def test_data_corruption_error_is_not_retried():
    op = _scripted_operation(DataCorruptionError("corrupt"))
    wrapped = with_connection_retry(op, max_attempts=3)

    with pytest.raises(DataCorruptionError, match="corrupt"):
        wrapped()

    assert len(op.calls) == 1


def test_valid_empty_result_is_not_retried_and_returned_unchanged():
    empty_result = []
    op = _scripted_operation(empty_result)
    wrapped = with_connection_retry(op, max_attempts=3)

    result = wrapped()

    assert result is empty_result
    assert len(op.calls) == 1


def test_non_empty_result_is_returned_unchanged_with_identity_preserved():
    payload = ["row1", "row2"]
    op = _scripted_operation(payload)
    wrapped = with_connection_retry(op, max_attempts=3)

    result = wrapped()

    assert result is payload


def test_args_and_kwargs_are_forwarded_identically_on_every_attempt():
    op = _scripted_operation(ConnectionError("first"), "ok")
    wrapped = with_connection_retry(op, max_attempts=3)

    result = wrapped(1, 2, foo="bar")

    assert result == "ok"
    assert op.calls[0] == {"args": (1, 2), "kwargs": {"foo": "bar"}}
    assert op.calls[1] == {"args": (1, 2), "kwargs": {"foo": "bar"}}


def test_keyword_only_start_end_ms_forwarding():
    op = _scripted_operation(ConnectionError("first"), [])
    wrapped = with_connection_retry(op, max_attempts=3)

    wrapped(start_time_ms=1000, end_time_ms=2000)

    for call in op.calls:
        assert call["args"] == ()
        assert call["kwargs"] == {"start_time_ms": 1000, "end_time_ms": 2000}


def test_max_attempts_bool_true_is_rejected():
    with pytest.raises(TypeError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts=True)


def test_max_attempts_bool_false_is_rejected():
    with pytest.raises(TypeError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts=False)


def test_max_attempts_float_is_rejected():
    with pytest.raises(TypeError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts=1.0)


def test_max_attempts_string_is_rejected():
    with pytest.raises(TypeError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts="3")


def test_max_attempts_zero_is_rejected():
    with pytest.raises(ValueError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts=0)


def test_max_attempts_negative_is_rejected():
    with pytest.raises(ValueError, match="max_attempts"):
        with_connection_retry(_scripted_operation("ok"), max_attempts=-1)


def test_final_connection_error_instance_identity_is_preserved():
    final_exc = ConnectionError("final")
    op = _scripted_operation(ConnectionError("first"), final_exc)
    wrapped = with_connection_retry(op, max_attempts=2)

    with pytest.raises(ConnectionError) as exc_info:
        wrapped()

    assert exc_info.value is final_exc
