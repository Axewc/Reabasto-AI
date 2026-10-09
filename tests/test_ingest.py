from datetime import date

import polars as pl

from reabasto.ingest.clean import (
    build_clean_sales,
    find_closed_days,
    item_units_of_measure,
    select_items,
    select_stores,
)
from reabasto.ingest.sap_export import build_export, inject_errors
from reabasto.quality.rules import Context, validate

START, END = date(2016, 1, 1), date(2016, 1, 10)


def raw(rows):
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "store_nbr": pl.Int16,
            "item_nbr": pl.Int32,
            "unit_sales": pl.Float64,
            "onpromotion": pl.Boolean,
        },
        orient="row",
    ).lazy()


def two_stores_ten_days(skip_store2=(), skip_all=()):
    rows = []
    for i in range(10):
        d = date(2016, 1, 1 + i)
        for store in (1, 2):
            if d in skip_all or (store == 2 and d in skip_store2):
                continue
            rows.append((d, store, 100, 5.0 + store, False))
            rows.append((d, store, 200, 1.0, True))
    return raw(rows)


def test_nationwide_closure_is_not_a_store_gap():
    closed = date(2016, 1, 1)
    sales = two_stores_ten_days(skip_all=(closed,))
    assert find_closed_days(sales, START, END) == [closed]
    picked = select_stores(sales, START, END, n_stores=2)
    assert set(picked["store_nbr"]) == {1, 2}


def test_store_missing_extra_days_is_excluded():
    sales = two_stores_ten_days(skip_store2=(date(2016, 1, 5),))
    picked = select_stores(sales, START, END, n_stores=2)
    assert picked["store_nbr"].to_list() == [1]


def test_items_must_be_present_in_every_selected_store():
    rows = [
        (date(2016, 1, 2), 1, 100, 3.0, False),
        (date(2016, 1, 2), 2, 100, 3.0, False),
        (date(2016, 1, 2), 1, 300, 9.0, False),
    ]
    items = pl.DataFrame(
        {
            "item_nbr": [100, 300],
            "family": ["DAIRY", "DAIRY"],
            "class": [1, 1],
            "perishable": [1, 1],
        }
    )
    chosen = select_items(raw(rows), items, [1, 2], START, END, top_per_family=10)
    assert chosen["item_nbr"].to_list() == [100]


def test_zero_days_rebuilt_returns_split_and_series_starts_at_first_sale():
    rows = [
        (date(2016, 1, 3), 1, 100, 4.0, False),
        (date(2016, 1, 5), 1, 100, -2.0, False),  # a return
        (date(2016, 1, 6), 1, 100, 3.0, True),
    ]
    dense = build_clean_sales(raw(rows), [1], [100], date(2016, 1, 7), [date(2016, 1, 7)])
    assert dense["date"].min() == date(2016, 1, 3)  # not before the first sale
    assert dense.height == 5  # Jan 3..7, no gaps
    by_day = {r["date"]: r for r in dense.iter_rows(named=True)}
    assert by_day[date(2016, 1, 4)]["unit_sales"] == 0 and by_day[date(2016, 1, 4)]["imputed_zero"]
    assert by_day[date(2016, 1, 5)]["unit_sales"] == 0 and by_day[date(2016, 1, 5)]["returns"] == 2
    assert by_day[date(2016, 1, 6)]["onpromotion"] is True
    assert by_day[date(2016, 1, 7)]["store_closed"] is True
    assert dense["unit_sales"].min() >= 0


def test_fractional_items_get_kg():
    rows = [(date(2016, 1, 2), 1, 100, 2.5, False), (date(2016, 1, 2), 1, 200, 3.0, False)]
    uom = item_units_of_measure(raw(rows), [100, 200]).sort("item_nbr")
    assert uom["uom"].to_list() == ["KG", "PC"]


def test_export_roundtrips_through_quality_rules():
    day = date(2016, 1, 4)
    n = 4
    sales = pl.DataFrame(
        {
            "date": [day] * n,
            "store_nbr": [1, 1, 2, 2],
            "item_nbr": [100, 101, 100, 101],
            "unit_sales": [4.0, 5.0, 6.0, 7.0],
            "returns": [0.0] * n,
            "onpromotion": [False] * n,
            "imputed_zero": [False] * n,
            "store_closed": [False] * n,
        }
    )
    items = pl.DataFrame({"item_nbr": [100, 101], "uom": ["PC", "PC"]})
    export = build_export(sales, items, day)
    ctx = Context(
        known_stores={1, 2},
        known_items={100, 101},
        expected_date=day,
        today=date(2016, 1, 5),
        uom_by_item={100: "PC", 101: "PC"},
    )
    assert validate(export.cast(pl.Utf8), ctx).accepted
    assert (export["LABST"] >= 0).all()

    broken = inject_errors(export)
    report = validate(broken.cast(pl.Utf8), ctx)
    assert not report.accepted
    assert {"Q04", "Q06", "Q07", "Q08"} <= {r.rule_id for r in report.failures}
