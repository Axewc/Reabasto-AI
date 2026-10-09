import json

from reabasto.config import ROOT, load_assumptions
from reabasto.ingest.clean import run_clean

a = load_assumptions()
summary = run_clean(
    slice_path=ROOT / "data/interim/train_slice.parquet",
    items_csv=ROOT / "data/csv/items.csv",
    stores_csv=ROOT / "data/csv/stores.csv",
    out_dir=ROOT / "data/clean",
    train_start=a.split.train_start,
    train_end=a.split.train_end,
    sim_end=a.split.sim_end,
    families=["GROCERY I", "DAIRY"],
)
print(json.dumps(summary, indent=2))
