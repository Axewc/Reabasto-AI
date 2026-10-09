"""Pick the store/item subset and turn the raw slice into a dense clean table.

Selection uses only the training window so no information from validation or
simulation leaks into which series we keep.
"""

import json
from datetime import date
from pathlib import Path

import polars as pl


def find_closed_days(sales: pl.LazyFrame, start: date, end: date, min_open_share: float = 0.5):
    """Dates on which fewer than `min_open_share` of the stores reported any sale.

    The raw file drops zero-sale rows, so a nationwide closure (Dec 25, Jan 1)
    shows up as a date almost every store is missing.
    """
    per_day = (
        sales.filter(pl.col("date").is_between(start, end))
        .group_by("date")
        .agg(pl.col("store_nbr").n_unique().alias("stores"))
        .collect()
    )
    threshold = per_day["stores"].max() * min_open_share
    open_days = set(per_day.filter(pl.col("stores") >= threshold)["date"].to_list())
    all_days = pl.date_range(start, end, eager=True).to_list()
    return [d for d in all_days if d not in open_days]


def select_stores(sales: pl.LazyFrame, start: date, end: date, n_stores: int = 5) -> pl.DataFrame:
    """Stores open every day of the training window except nationwide closures.

    Among those, keep the `n_stores` with the most units sold.
    """
    closed = set(find_closed_days(sales, start, end))
    expected = {d for d in pl.date_range(start, end, eager=True).to_list() if d not in closed}
    per_store = (
        sales.filter(pl.col("date").is_between(start, end))
        .group_by("store_nbr")
        .agg(
            pl.col("date").unique().alias("dates"),
            pl.col("unit_sales").clip(lower_bound=0).sum().alias("train_units"),
        )
        .collect()
    )
    rows = []
    for store, dates, units in per_store.iter_rows():
        missing = len(expected - set(dates))
        rows.append({"store_nbr": store, "train_units": units, "missing_days": missing})
    stores = pl.DataFrame(
        rows, schema={"store_nbr": pl.Int16, "train_units": pl.Float64, "missing_days": pl.Int64}
    )
    return (
        stores.filter(pl.col("missing_days") == 0)
        .sort("train_units", descending=True)
        .head(n_stores)
    )


def select_items(
    sales: pl.LazyFrame,
    items: pl.DataFrame,
    stores: list[int],
    start: date,
    end: date,
    top_per_family: int = 100,
) -> pl.DataFrame:
    """Top items per family by training units, present in every selected store."""
    agg = (
        sales.filter(pl.col("date").is_between(start, end) & pl.col("store_nbr").is_in(stores))
        .group_by("item_nbr")
        .agg(
            pl.col("store_nbr").n_unique().alias("n_stores"),
            pl.col("unit_sales").clip(lower_bound=0).sum().alias("train_units"),
        )
        .filter(pl.col("n_stores") == len(stores))
        .collect()
    )
    return (
        agg.join(items, on="item_nbr", how="inner")
        .sort("train_units", descending=True)
        .group_by("family", maintain_order=True)
        .head(top_per_family)
        .sort("family", "train_units", descending=[False, True])
    )


def build_clean_sales(
    sales: pl.LazyFrame,
    stores: list[int],
    item_ids: list[int],
    end: date,
    closed_days: list[date] | None = None,
) -> pl.DataFrame:
    """Dense daily table: zero days rebuilt, returns split from demand.

    Each series starts at its first observed sale (an item not yet in the
    assortment is not zero demand) and runs to `end`.
    """
    obs = (
        sales.filter(pl.col("store_nbr").is_in(stores) & pl.col("item_nbr").is_in(item_ids))
        .filter(pl.col("date") <= end)
        .with_columns(
            pl.col("unit_sales").clip(lower_bound=0).alias("units"),
            (-pl.col("unit_sales")).clip(lower_bound=0).alias("returns"),
        )
        .select("date", "store_nbr", "item_nbr", "units", "returns", "onpromotion")
        .collect()
    )
    firsts = obs.group_by("store_nbr", "item_nbr").agg(pl.col("date").min().alias("first"))
    grid = (
        firsts.with_columns(
            pl.date_ranges(pl.col("first"), pl.lit(end), interval="1d").alias("date")
        )
        .explode("date")
        .select("date", "store_nbr", "item_nbr")
    )
    dense = (
        grid.join(obs, on=["date", "store_nbr", "item_nbr"], how="left")
        .with_columns(
            pl.col("units").is_null().alias("imputed_zero"),
            pl.col("units").fill_null(0.0).alias("unit_sales"),
            pl.col("returns").fill_null(0.0),
            pl.col("onpromotion").fill_null(False),
            pl.col("date").is_in(closed_days or []).alias("store_closed"),
        )
        .select(
            "date",
            "store_nbr",
            "item_nbr",
            "unit_sales",
            "returns",
            "onpromotion",
            "imputed_zero",
            "store_closed",
        )
        .sort("store_nbr", "item_nbr", "date")
    )
    return dense


def item_units_of_measure(sales: pl.LazyFrame, item_ids: list[int]) -> pl.DataFrame:
    """KG when the item was ever sold in fractional quantities, PC otherwise."""
    return (
        sales.filter(pl.col("item_nbr").is_in(item_ids))
        .group_by("item_nbr")
        .agg(((pl.col("unit_sales") % 1) != 0).any().alias("fractional"))
        .with_columns(
            pl.when(pl.col("fractional")).then(pl.lit("KG")).otherwise(pl.lit("PC")).alias("uom")
        )
        .select("item_nbr", "uom")
        .collect()
    )


def run_clean(
    slice_path: Path,
    items_csv: Path,
    stores_csv: Path,
    out_dir: Path,
    train_start: date,
    train_end: date,
    sim_end: date,
    families: list[str],
    n_stores: int = 5,
    top_per_family: int = 100,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    sales = pl.scan_parquet(slice_path)
    items = pl.read_csv(items_csv).filter(pl.col("family").is_in(families))
    stores_meta = pl.read_csv(stores_csv)

    closed = find_closed_days(sales, train_start, train_end)
    stores = select_stores(sales, train_start, train_end, n_stores)
    store_ids = stores["store_nbr"].to_list()
    chosen = select_items(sales, items, store_ids, train_start, train_end, top_per_family)
    item_ids = chosen["item_nbr"].to_list()

    dense = build_clean_sales(sales, store_ids, item_ids, sim_end, closed)
    uom = item_units_of_measure(sales, item_ids)
    items_out = chosen.join(uom, on="item_nbr").select(
        "item_nbr", "family", "class", "perishable", "uom", "train_units"
    )
    stores_out = stores.join(stores_meta, on="store_nbr").select(
        "store_nbr", "city", "state", "type", "cluster", "train_units", "missing_days"
    )

    dense.write_parquet(out_dir / "sales.parquet", compression="zstd")
    items_out.write_parquet(out_dir / "items.parquet")
    stores_out.write_parquet(out_dir / "stores.parquet")

    summary = {
        "closed_days": [d.isoformat() for d in closed],
        "stores": store_ids,
        "n_items": len(item_ids),
        "n_series": dense.select(pl.struct("store_nbr", "item_nbr").n_unique()).item(),
        "rows": dense.height,
        "imputed_zero_rows": int(dense["imputed_zero"].sum()),
        "return_rows": int((dense["returns"] > 0).sum()),
        "kg_items": int((items_out["uom"] == "KG").sum()),
    }
    (out_dir / "selection.json").write_text(json.dumps(summary, indent=2))
    return summary
