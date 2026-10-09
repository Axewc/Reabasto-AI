import csv
import time

from reabasto.config import ROOT, load_assumptions
from reabasto.ingest.slice import stream_slice

FAMILIES = {"GROCERY I", "DAIRY"}
a = load_assumptions()
items = [
    int(r["item_nbr"])
    for r in csv.DictReader(open(ROOT / "data/csv/items.csv"))
    if r["family"] in FAMILIES
]
t = time.time()
stats = stream_slice(
    ROOT / "data/raw/train.csv.7z",
    ROOT / "data/interim/train_slice.parquet",
    items,
    a.split.train_start,
)
print(len(items), "items;", stats, f"{time.time() - t:.0f}s")
