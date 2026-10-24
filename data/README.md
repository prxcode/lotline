# Data

Everything Lotline reads is in this folder. It plays the food bank's systems: the app treats each CSV as one table (locally through DuckDB, or registered in Zetaris).

| File | Role | Rows |
|---|---|---|
| `recalls.csv` | Recall notices: company, brand, reason, date, status, link to the FDA page | 19 |
| `recall_products.csv` | One row per recalled product line: UPCs, lot or product codes (`ALL` = every lot), best-by dates, other names the notice prints | 39 |
| `inventory.csv` | Stock as volunteers logged it: brand and product as entered, UPC, lot, best-by, quantity received, shelf location | 180 |
| `distributions.csv` | Shipments to partner agencies | 115 |
| `agencies.csv` | Partner agencies and contacts (emails on the reserved `.example` domain) | 16 |
| `answer_key.csv` | Expected verdict per item, for `eval.py` only. The app never reads it | 103 |

## Two kinds of recall

| IDs | What | Source |
|---|---|---|
| `R-001` to `R-010` | Real FDA recalls from 2026, transcribed from the notices | Public FDA pages; stock, shipments and agencies are invented; answer key written independently of Lotline's rules |
| `R-011` to `R-014` | Real FDA recalls from October 2026 for products this food bank does not hold | Public FDA pages, checked 22 Oct 2026. No answer-key rows: nothing in stock matches, which a test checks |
| `RC-2024-001` to `RC-2024-005` | Fictional brands and recalls (Hollow Creek Farms and others) | Written with the rules as a regression set |

Both share one food bank: one inventory, one shipment log, one agency list.

## Recalls R-011 to R-014 (added 22 Oct 2026)

Transcribed from the FDA pages linked in `recalls.csv`, as printed:

| Recall | Notice | Transcribed |
|---|---|---|
| R-011 Gelatelli (Lidl US) | Coffee & Vanilla Gelato, 16 fl oz, UPC 4 0564897 22458, lot 085261, best by MAR/26/2028, Listeria | One line, all fields |
| R-012 Family Farmstead | Raw Mild Cheddar Cheese, 8 oz; lot and best by "7/22/27"; no UPC listed; E. coli O26:H11 and O45:H2 | Lot `7/22/27`, best by 2027-07-22 |
| R-013 Stewart's Shops | Heavenly Medley (UPC 0 82086 44371 1, best by Feb.10.27) and Vanilla-Chocolate-Strawberry (UPC 0 82086 44377 3, best by Feb.11.27), half gallon; no lot listed; metal | Two lines |
| R-014 WinCo Foods | Salsa Bean Dip Tray Express 54 oz (UPC 2-67173-#####) and 16 Inch Catering Tray 104 oz (UPC 2-67240-#####), best if used by Aug. 14 to Sept. 25, 2026; contains the recalled Mezzetta peperoncini (R-010) | Two lines; the UPCs are variable-weight prefixes, matched as printed |

A fifth candidate from the same weeks, Eden Brothers broccoli seeds, was left out: garden seeds are not food bank stock.

## Answer key columns

`verdict` is `AFFECTED` (pull), `NEEDS_CHECK` (a person must look) or `NOT_AFFECTED`. `agencies_that_received_it` lists agency IDs with quantities. `quantity_still_in_warehouse` is filled for the FDA recalls. Rows whose `item_id` starts with `DIST-` are shipments the trace must find.

## How the real recalls were checked

The FDA rows came from a dataset delivered as one row per recall. Each was checked against its FDA page and rebuilt as product lines, because real notices list several products, each with its own codes:

| Recall | As delivered | In the FDA notice | Change |
|---|---|---|---|
| R-001 Nature's Own | Five product-code ranges applied to both UPCs | Codes apply to the 20 oz loaf only; the 40 oz 2-pack has codes "N/A" | Two lines; a match on the 2-pack's UPC goes to a person |
| R-002 Falcon Trading | Product called "Sunset Soup Mix Organic" | "soup Mix Organic"; item numbers printed as "SRF item #" | Three lines, all lots; "SRF" and "Sunridge Farms" kept as names the notice prints |
| R-004 SkinnyDipped | One lot list, best-by range 12/20 to 12/29 | Seven pack sizes, each with its own lots; dates 12/20, 22, 23, 26, 27, 29 (a list, not a range); recall since terminated | Seven lines with date lists; status closed; Amazon ID dropped (not a UPC) |
| R-006 Genova | Best-by range 1/17 to 1/24 | Each can code has one date | Three lines; UPCs kept as printed (10 digits) |
| R-007 snack mixes | 3 UPCs, one range across brands | 8 products, each with its own UPC and dates | Eight lines |
| R-008 Kroger | Best-by range 2/17 to 4/7 | Nine specific dates | Date list |
| R-003, R-005, R-009, R-010 | | Match | Same values, line format |

Other changes: inventory `quantity` renamed `quantity_received` (on hand = received minus shipped); agencies given `contact_email` and `active`.

## Merging the two sets

The fictional set used different column names. It was converted to the same columns: `brand` to `brand_as_entered`, `location` to `warehouse_location`, `dist_id` to `distribution_id`, on-hand quantity to quantity received (on hand plus shipped). Its answer key was converted to the same `verdict` format. The original key left out two shipments of affected RC-2024-003 stock (DIST-008, DIST-010) that the shipment log records; they were added.

## Evaluation protocol for the real recalls

1. R-001 to R-005 were the development set, used to design the rules.
2. R-006 to R-010 were held out: only their notice facts were checked; their stock and answers were not read until the rules were frozen.
3. One blind run on the held-out set: **27 of 30 items, 0 unsafe misses, agencies 13/13, on-hand quantities 13/13.**
4. The three disagreements were checked against the notices. Two were real gaps (a stated pack size that conflicts with the notice; lot code and best-by date agreeing without a UPC) and were fixed with general rules. The third (brand typed "Mezzeta", no UPC) is sent to a person on purpose.
5. After the fixes the held-out set is no longer blind; 27/30 is the honest held-out number.
