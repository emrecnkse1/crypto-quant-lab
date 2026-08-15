import inspect

from crypto_quant_lab.funding.store import HistoricalFundingStore


def test_protocol_defines_required_contract():
    for method_name in ("write_ingestion_batch", "query_events", "query_coverage", "close"):
        assert hasattr(HistoricalFundingStore, method_name)

    write_params = list(inspect.signature(HistoricalFundingStore.write_ingestion_batch).parameters)
    assert write_params == [
        "self",
        "events",
        "exchange",
        "market_type",
        "symbol",
        "covered_start",
        "covered_end",
    ]

    query_events_params = list(inspect.signature(HistoricalFundingStore.query_events).parameters)
    assert query_events_params == [
        "self",
        "exchange",
        "market_type",
        "symbol",
        "start_time",
        "end_time",
    ]

    query_coverage_params = list(
        inspect.signature(HistoricalFundingStore.query_coverage).parameters
    )
    assert query_coverage_params == [
        "self",
        "exchange",
        "market_type",
        "symbol",
        "start_time",
        "end_time",
    ]

    close_params = set(inspect.signature(HistoricalFundingStore.close).parameters)
    assert close_params == {"self"}


def test_write_ingestion_batch_events_is_positional_or_keyword():
    signature = inspect.signature(HistoricalFundingStore.write_ingestion_batch)
    assert signature.parameters["events"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD


def test_write_ingestion_batch_metadata_and_range_are_keyword_only():
    signature = inspect.signature(HistoricalFundingStore.write_ingestion_batch)
    for name in ("exchange", "market_type", "symbol", "covered_start", "covered_end"):
        assert signature.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_query_events_params_are_keyword_only():
    signature = inspect.signature(HistoricalFundingStore.query_events)
    for name in ("exchange", "market_type", "symbol", "start_time", "end_time"):
        assert signature.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_query_coverage_params_are_keyword_only():
    signature = inspect.signature(HistoricalFundingStore.query_coverage)
    for name in ("exchange", "market_type", "symbol", "start_time", "end_time"):
        assert signature.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
