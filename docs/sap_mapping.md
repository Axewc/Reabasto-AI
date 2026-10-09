# SAP field mapping

The daily export written by `reabasto.ingest.sap_export` uses standard SAP ECC/S4
field names so the downstream pipeline reads it as it would a real ERP extract.

| SAP field | SAP table | Meaning | Source in this project |
| --- | --- | --- | --- |
| FKDAT | VBRK | Billing date (`YYYYMMDD`) | `date` |
| WERKS | VBRP | Plant (store) | `store_nbr` |
| MATNR | VBRP | Material number | `item_nbr` |
| FKIMG | VBRP | Billed quantity (net of returns; negative = return) | `unit_sales - returns` |
| MEINS | VBRP | Base unit of measure (`KG` for items sold by weight, `PC` otherwise) | derived: `KG` if the item was ever sold in fractional quantities |
| LABST | MARD | Unrestricted-use stock | **simulated** (seeded multiple of the day's sales) |

## Notes

- In a real ERP, sales (VBRK/VBRP) and stock (MARD) come from different tables and
  the extractor joins them. Here they share one file to keep the demo small.
- LABST is not in the Favorita dataset. It is generated with a fixed seed so runs
  are reproducible, and it is never used to compute the impact figure: the
  simulation starts from the policy's own order-up-to level.
- Other ERPs (for example SAP Business One) use different field names. Only this
  mapping changes; the quality rules and the rest of the pipeline do not.
- `--inject-errors` writes a file with a duplicate key, an unknown plant, negative
  stock and a fractional quantity under `PC`, to exercise the rejection path.

## Quality rules applied on ingest

| Id | Severity | Rule |
| --- | --- | --- |
| Q01 | blocking | required columns present |
| Q02 | blocking | required fields not null |
| Q03 | blocking | values parse (date `YYYYMMDD`, numeric quantities, integer ids) |
| Q04 | blocking | plant and material exist in master data |
| Q05 | blocking | billing date is the expected one and not in the future |
| Q06 | blocking | no duplicate (MATNR, WERKS, FKDAT) |
| Q07 | blocking | fractional quantities only with `KG`, and unit matches the master |
| Q08 | blocking | LABST is not negative |
| Q09 | warning | negative FKIMG (returns, split from demand) |
| Q10 | warning | row count within ±30% of the 28-day average |

Score = severity-weighted share of rules that pass (blocking = 3, warning = 1).
Any blocking failure rejects the load with its reasons.
