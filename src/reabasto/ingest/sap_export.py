"""Write one day of clean sales as an ERP-style flat export (SAP field names)."""

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from reabasto.config import ROOT

COLUMNS = ["FKDAT", "WERKS", "MATNR", "FKIMG", "MEINS", "LABST"]


def build_export(
    sales: pl.DataFrame, items: pl.DataFrame, day: date, seed: int = 7
) -> pl.DataFrame:
    """One row per store/material for `day`.

    FKIMG is the net invoiced quantity (returns come through as negatives, as in
    the raw file). LABST is simulated: the dataset has no stock, so it is a
    seeded multiple of the day's sales.
    """
    rng = np.random.default_rng(seed + day.toordinal())
    day_rows = sales.filter((pl.col("date") == day) & ~pl.col("imputed_zero"))
    out = (
        day_rows.join(items.select("item_nbr", "uom"), on="item_nbr", how="left")
        .with_columns((pl.col("unit_sales") - pl.col("returns")).alias("net"))
        .sort("store_nbr", "item_nbr")
    )
    cover = rng.uniform(2.0, 6.0, size=out.height)
    stock = np.ceil(np.clip(out["net"].to_numpy(), 0, None) * cover) + rng.integers(
        0, 4, out.height
    )
    return out.select(
        pl.col("date").dt.strftime("%Y%m%d").alias("FKDAT"),
        pl.col("store_nbr").cast(pl.Utf8).alias("WERKS"),
        pl.col("item_nbr").cast(pl.Utf8).alias("MATNR"),
        pl.col("net").alias("FKIMG"),
        pl.col("uom").alias("MEINS"),
        pl.Series("LABST", stock),
    )


def inject_errors(df: pl.DataFrame) -> pl.DataFrame:
    """Corrupt a few rows so the quality rules have something to reject."""
    if df.height < 4:
        raise ValueError("need at least 4 rows to inject the four error kinds")
    rows = df.to_dicts()
    rows.append(dict(rows[0]))  # duplicate key
    rows[1]["LABST"] = -5.0  # negative stock
    rows[2]["WERKS"] = "9999"  # unknown store
    rows[3]["FKIMG"] = 2.5  # fraction with PC
    rows[3]["MEINS"] = "PC"
    return pl.DataFrame(rows, schema=df.schema)


def write_export(df: pl.DataFrame, day: date, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ventas_{day.isoformat()}.csv"
    df.write_csv(path)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", required=True, type=date.fromisoformat)
    p.add_argument("--out", type=Path, default=ROOT / "inbox")
    p.add_argument("--inject-errors", action="store_true")
    args = p.parse_args()

    sales = pl.read_parquet(ROOT / "data/clean/sales.parquet")
    items = pl.read_parquet(ROOT / "data/clean/items.parquet")
    df = build_export(sales, items, args.date)
    if args.inject_errors:
        df = inject_errors(df)
    print(write_export(df, args.date, args.out), f"({df.height} rows)")


if __name__ == "__main__":
    main()
