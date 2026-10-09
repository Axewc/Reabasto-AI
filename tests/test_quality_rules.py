from datetime import date

import polars as pl
import pytest

from reabasto.quality.rules import Context, validate

DAY = date(2017, 5, 24)


def good() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "FKDAT": ["20170524"] * 3,
            "WERKS": ["44", "44", "45"],
            "MATNR": ["100", "101", "100"],
            "FKIMG": ["5", "2.5", "7"],
            "MEINS": ["PC", "KG", "PC"],
            "LABST": ["12", "8", "20"],
        }
    )


@pytest.fixture
def ctx() -> Context:
    return Context(
        known_stores={44, 45},
        known_items={100, 101},
        expected_date=DAY,
        today=date(2017, 5, 25),
        uom_by_item={100: "PC", 101: "KG"},
        recent_row_counts=[3] * 28,
    )


def failed_ids(df, ctx):
    return {r.rule_id for r in validate(df, ctx).failures}


def test_clean_load_is_accepted_with_full_score(ctx):
    report = validate(good(), ctx)
    assert report.accepted and report.score == 1.0 and not report.failures


def test_missing_column_rejects_the_load(ctx):
    report = validate(good().drop("LABST"), ctx)
    assert not report.accepted and report.results[0].rule_id == "Q01"


def test_null_required_field(ctx):
    df = good().with_columns(
        pl.when(pl.col("WERKS") == "44").then(None).otherwise(pl.col("WERKS")).alias("WERKS")
    )
    assert "Q02" in failed_ids(df, ctx)


def test_bad_type(ctx):
    df = good().with_columns(pl.Series("FKIMG", ["5", "abc", "7"]))
    assert "Q03" in failed_ids(df, ctx)


def test_unknown_store_or_material(ctx):
    df = good().with_columns(pl.Series("WERKS", ["44", "99", "45"]))
    assert "Q04" in failed_ids(df, ctx)


def test_wrong_or_future_date(ctx):
    wrong = good().with_columns(pl.lit("20170523").alias("FKDAT"))
    future = good().with_columns(pl.lit("20170601").alias("FKDAT"))
    assert "Q05" in failed_ids(wrong, ctx)
    assert "Q05" in failed_ids(future, ctx)


def test_duplicate_keys(ctx):
    df = pl.concat([good(), good().head(1)])
    assert "Q06" in failed_ids(df, ctx)


def test_fraction_with_piece_unit(ctx):
    df = good().with_columns(pl.Series("FKIMG", ["5.5", "2.5", "7"]))
    assert "Q07" in failed_ids(df, ctx)


def test_unit_must_match_master(ctx):
    df = good().with_columns(pl.Series("MEINS", ["PC", "PC", "PC"]))
    assert "Q07" in failed_ids(df, ctx)


def test_negative_stock(ctx):
    df = good().with_columns(pl.Series("LABST", ["12", "-1", "20"]))
    assert "Q08" in failed_ids(df, ctx)


def test_return_is_a_warning_not_a_rejection(ctx):
    df = good().with_columns(pl.Series("FKIMG", ["-3", "2.5", "7"]))
    report = validate(df, ctx)
    assert report.accepted
    assert {r.rule_id for r in report.failures} == {"Q09"}
    assert report.score < 1.0


def test_row_count_anomaly_is_a_warning(ctx):
    ctx.recent_row_counts = [100] * 28
    report = validate(good(), ctx)
    assert report.accepted and {r.rule_id for r in report.failures} == {"Q10"}


def test_blocking_failure_lowers_score_and_rejects(ctx):
    df = good().with_columns(pl.Series("LABST", ["12", "-1", "20"]))
    report = validate(df, ctx)
    assert not report.accepted and report.score < 1.0
    assert any("Q08" in r for r in report.reasons())
