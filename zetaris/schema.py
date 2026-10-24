import re

# Canonical fields each role needs, and the column names food bank systems actually use for them
SYNONYMS = {
    "stock": {
        "item_id": ["item_id", "sku", "item_no", "item", "id"],
        "brand": ["brand", "brand_as_entered", "brand_name", "manufacturer"],
        "product": ["product", "product_name_as_entered", "product_name", "item_description", "description"],
        "upc": ["upc", "gtin", "barcode", "upc_code"],
        "lot_code": ["lot_code", "lot", "lot_number", "product_code", "code"],
        "best_by": ["best_by", "best_by_date", "best_before", "use_by", "expiry", "expiration_date"],
        "quantity_received": ["quantity_received", "qty_received", "received_qty"],
        "quantity_on_hand": ["quantity_on_hand", "on_hand", "quantity", "qty"],
        "unit": ["unit", "uom"],
        "location": ["location", "warehouse_location", "bin", "shelf"],
        "received_date": ["received_date", "date_received", "received"],
        "source": ["source_type", "donor_or_supplier", "donor", "channel", "source"],
        "notes": ["notes", "note", "comments"],
    },
    "shipments": {
        "ship_id": ["dist_id", "distribution_id", "shipment_id", "ship_id"],
        "item_id": ["item_id", "sku", "item_no"],
        "agency_id": ["agency_id", "partner_id"],
        "quantity": ["quantity", "qty", "quantity_shipped"],
        "unit": ["unit", "uom"],
        "ship_date": ["distributed_date", "ship_date", "shipped_date", "date"],
    },
    "contacts": {
        "agency_id": ["agency_id", "partner_id"],
        "name": ["agency_name", "partner_name", "name"],
        "contact_name": ["contact_name", "contact"],
        "contact_email": ["contact_email", "email"],
        "contact_phone": ["contact_phone", "phone"],
        "active": ["active", "is_active"],
    },
    "notices": {
        "recall_id": ["recall_id"],
        "recall_number": ["recall_number"],
        "source": ["source"],
        "company": ["company", "recalling_firm"],
        "brand": ["brand"],
        "product": ["product", "product_description"],
        "reason": ["reason", "reason_for_recall"],
        "announced_date": ["announced_date", "date_announced", "recall_initiation_date"],
        "status": ["status"],
        "notice_url": ["notice_url", "url"],
    },
    "notice_lines": {
        "recall_id": ["recall_id"],
        "line_id": ["line_id"],
        "brand": ["brand"],
        "product": ["product", "product_description"],
        "size": ["size", "package_size"],
        "upc": ["upc", "upcs"],
        "lot_codes": ["lot_codes", "codes", "code_info"],
        "best_by": ["best_by", "best_by_dates"],
        "other_names": ["other_names"],
    },
}

# Fields a table must have to play a role, checked in this order
REQUIRED = [
    ("notice_lines", ["recall_id", "lot_codes", "upc"]),
    ("notices", ["recall_id", "reason"]),
    ("shipments", ["ship_id", "item_id", "agency_id", "quantity"]),
    ("contacts", ["agency_id", "name", "contact_name"]),
    ("stock", ["item_id", "product"]),
]
ROLE_TEXT = {
    "stock": "Stock received, with lot codes and shelf locations",
    "shipments": "Product sent out to partner agencies",
    "contacts": "Who to contact at each partner agency",
    "notices": "Recall notices",
    "notice_lines": "Recalled product lines: UPCs, lot codes, best-by dates",
}


def _norm(col: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", col.lower()).strip("_")


def map_columns(role: str, columns: list[str]) -> dict[str, str]:
    """canonical field -> actual column, taking each column at most once."""
    by_norm = {_norm(c): c for c in columns}
    used, out = set(), {}
    for field, names in SYNONYMS[role].items():
        for n in names:
            col = by_norm.get(n)
            if col and col not in used:
                out[field] = col
                used.add(col)
                break
    return out


def detect_role(columns: list[str]) -> tuple[str, dict[str, str]]:
    for role, need in REQUIRED:
        mapping = map_columns(role, columns)
        if all(f in mapping for f in need):
            if role == "stock" and not ({"quantity_received", "quantity_on_hand"} & set(mapping)):
                continue
            return role, mapping
    return "other", {}


def validate(role: str, columns: list[str], mapping: dict[str, str]) -> list[str]:
    """Problems with a proposed mapping; empty means it can be used."""
    problems = [f"{f} -> {c}: no such column" for f, c in mapping.items() if c not in columns]
    problems += [f"{f} is not a field of {role}" for f in mapping if f not in SYNONYMS.get(role, {})]
    need = dict(REQUIRED).get(role, [])
    problems += [f"missing required field {f}" for f in need if f not in mapping]
    if role == "stock" and not ({"quantity_received", "quantity_on_hand"} & set(mapping)):
        problems.append("missing a quantity field")
    return problems
