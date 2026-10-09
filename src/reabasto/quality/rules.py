"""Data-quality rules for the daily SAP-style sales export.

Each rule has an id, a severity and a message, and returns the number of rows
that violate it. A blocking failure rejects the whole load; warnings only lower
the score. The score is the severity-weighted share of rules that pass.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

import polars as pl

REQUIRED = ["FKDAT", "WERKS", "MATNR", "FKIMG", "MEINS", "LABST"]
WEIGHTS = {"blocking": 3, "warning": 1}
KEY = ["MATNR", "WERKS", "FKDAT"]


@dataclass
class Context:
    """Reference data a load is validated against."""

    known_stores: set[int]
    known_items: set[int]
    expected_date: date | None = None
    today: date = field(default_factory=date.today)
    uom_by_item: dict[int, str] = field(default_factory=dict)
    recent_row_counts: list[int] = field(default_factory=list)
    row_count_tolerance: float = 0.30


@dataclass
class RuleResult:
    rule_id: str
    severity: str
    message: str
    failed_rows: int
    passed: bool


@dataclass
class LoadReport:
    score: float
    accepted: bool
    rows: int
    results: list[RuleResult]

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed]

    def reasons(self) -> list[str]:
        return [f"{r.rule_id}: {r.message} ({r.failed_rows} rows)" for r in self.failures]


@dataclass
class Rule:
    rule_id: str
    severity: str
    message: str
    check: Callable[[pl.DataFrame, Context], int]


def _parsed_date(df: pl.DataFrame) -> pl.Series:
    return df["FKDAT"].cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)


def _num(df: pl.DataFrame, col: str) -> pl.Series:
    return df[col].cast(pl.Utf8).cast(pl.Float64, strict=False)


def _int(df: pl.DataFrame, col: str) -> pl.Series:
    return df[col].cast(pl.Utf8).cast(pl.Int64, strict=False)


def r_not_null(df: pl.DataFrame, ctx: Context) -> int:
    mask = pl.fold(False, lambda a, b: a | b, [pl.col(c).is_null() for c in REQUIRED])
    return int(df.select(mask.sum()).item())


def r_types(df: pl.DataFrame, ctx: Context) -> int:
    bad = (
        _parsed_date(df).is_null()
        | _num(df, "FKIMG").is_null()
        | _num(df, "LABST").is_null()
        | _int(df, "WERKS").is_null()
        | _int(df, "MATNR").is_null()
    )
    # null values are reported by r_not_null, not here
    nulls = pl.fold(False, lambda a, b: a | b, [pl.col(c).is_null() for c in REQUIRED])
    return int((bad & ~df.select(nulls).to_series()).sum())


def r_master_data(df: pl.DataFrame, ctx: Context) -> int:
    store_ok = _int(df, "WERKS").is_in(list(ctx.known_stores))
    item_ok = _int(df, "MATNR").is_in(list(ctx.known_items))
    return int((~(store_ok & item_ok)).sum())


def r_date(df: pl.DataFrame, ctx: Context) -> int:
    d = _parsed_date(df)
    bad = d > ctx.today
    if ctx.expected_date is not None:
        bad = bad | (d != ctx.expected_date)
    return int(bad.fill_null(False).sum())


def r_duplicates(df: pl.DataFrame, ctx: Context) -> int:
    return int(df.select(pl.struct(KEY).is_duplicated().sum()).item())


def r_returns(df: pl.DataFrame, ctx: Context) -> int:
    return int((_num(df, "FKIMG") < 0).sum())


def r_fraction_uom(df: pl.DataFrame, ctx: Context) -> int:
    qty = _num(df, "FKIMG")
    fractional = (qty % 1) != 0
    not_kg = df["MEINS"].cast(pl.Utf8) != "KG"
    bad = fractional & not_kg
    if ctx.uom_by_item:
        master = _int(df, "MATNR").replace_strict(
            list(ctx.uom_by_item), list(ctx.uom_by_item.values()), default=None
        )
        bad = bad | ((master.is_not_null()) & (master != df["MEINS"].cast(pl.Utf8)))
    return int(bad.fill_null(False).sum())


def r_stock_non_negative(df: pl.DataFrame, ctx: Context) -> int:
    return int((_num(df, "LABST") < 0).sum())


def r_row_count(df: pl.DataFrame, ctx: Context) -> int:
    if len(ctx.recent_row_counts) < 7:
        return 0
    avg = sum(ctx.recent_row_counts) / len(ctx.recent_row_counts)
    off = abs(df.height - avg) / avg > ctx.row_count_tolerance
    return df.height if off else 0


RULES = [
    Rule("Q02", "blocking", "required field is null", r_not_null),
    Rule("Q03", "blocking", "value has the wrong type or format", r_types),
    Rule("Q04", "blocking", "store or material not in master data", r_master_data),
    Rule("Q05", "blocking", "invoice date is not the expected one or is in the future", r_date),
    Rule("Q06", "blocking", "duplicate (MATNR, WERKS, FKDAT)", r_duplicates),
    Rule("Q07", "blocking", "fractional quantity with a non-KG unit of measure", r_fraction_uom),
    Rule("Q08", "blocking", "negative stock (LABST)", r_stock_non_negative),
    Rule("Q09", "warning", "negative quantity treated as a return", r_returns),
    Rule("Q10", "warning", "row count far from the 28-day average", r_row_count),
]


def validate(df: pl.DataFrame, ctx: Context) -> LoadReport:
    """Run every rule. Schema problems short-circuit: nothing else is checkable."""
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        schema_fail = RuleResult("Q01", "blocking", f"missing columns: {missing}", df.height, False)
        return LoadReport(score=0.0, accepted=False, rows=df.height, results=[schema_fail])

    results = [RuleResult("Q01", "blocking", "required columns present", 0, True)]
    for rule in RULES:
        failed = rule.check(df, ctx)
        results.append(RuleResult(rule.rule_id, rule.severity, rule.message, failed, failed == 0))

    total = sum(WEIGHTS[r.severity] for r in results)
    got = sum(WEIGHTS[r.severity] for r in results if r.passed)
    blocked = any(r.severity == "blocking" and not r.passed for r in results)
    return LoadReport(
        score=round(got / total, 3), accepted=not blocked, rows=df.height, results=results
    )


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()
