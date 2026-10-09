"""Stream a date/item slice out of the compressed Kaggle train file.

The full train.csv is ~5 GB uncompressed and the laptop has little free disk, so
the archive is decompressed to a pipe and filtered batch by batch; only the
slice is ever written (as Parquet).
"""

import subprocess
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

TRAIN_COLUMNS = {
    "id": pa.int64(),
    "date": pa.date32(),
    "store_nbr": pa.int16(),
    "item_nbr": pa.int32(),
    "unit_sales": pa.float64(),
    "onpromotion": pa.bool_(),
}


def stream_slice(
    archive: Path,
    out_path: Path,
    item_ids: list[int],
    start: date,
    sevenzip: str = "7zz",
) -> dict:
    """Write rows with date >= start and item_nbr in item_ids to out_path.

    Returns row counts so the caller can log what was kept.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wanted = pa.array(item_ids, type=pa.int32())
    proc = subprocess.Popen(
        [sevenzip, "e", "-so", "-bso0", "-bsp0", str(archive)],
        stdout=subprocess.PIPE,
    )
    reader = pacsv.open_csv(
        proc.stdout,
        read_options=pacsv.ReadOptions(block_size=1 << 24),
        convert_options=pacsv.ConvertOptions(column_types=TRAIN_COLUMNS),
    )
    seen = kept = 0
    writer = None
    try:
        for batch in reader:
            seen += batch.num_rows
            table = pa.Table.from_batches([batch]).drop_columns(["id"])
            mask = pc.and_(
                pc.greater_equal(table["date"], pa.scalar(start, pa.date32())),
                pc.is_in(table["item_nbr"], value_set=wanted),
            )
            table = table.filter(mask)
            if table.num_rows == 0:
                continue
            if writer is None:
                writer = pq.ParquetWriter(out_path, table.schema, compression="zstd")
            writer.write_table(table)
            kept += table.num_rows
    finally:
        if writer is not None:
            writer.close()
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"7zz exited with code {rc}")
    return {"rows_seen": seen, "rows_kept": kept}
