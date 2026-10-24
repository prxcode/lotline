from agents.criteria import upc_keys, lot_status, date_status, product_match, brand_in_text, is_ambiguous, size_conflict
from agents.resolve import norm, brand_similarity, FUZZY_MIN

VERDICT_RANK = {"NO_MATCH": 0, "NEAR_MISS": 1, "HUMAN_CHECK": 2, "PROBABLE_MATCH": 3, "CONFIRMED_MATCH": 4}
VERDICT_ACTION = {
    "CONFIRMED_MATCH": "pull_and_notify",
    "PROBABLE_MATCH": "human_verify",
    "HUMAN_CHECK": "human_verify",
    "NEAR_MISS": "monitor",
    "NO_MATCH": "clear",
}
# A spelling the model suggested finds an item but never proves it: only these can confirm a pull without a UPC
STRONG_BRAND = {"exact", "learned", "notice", "in_name"}
# Below this share of the notice's naming words, a product without a barcode is not confirmed
NAME_COVERAGE_MIN = 0.75
NEAR_MISS_MAX = 5

__all__ = ["evaluate", "brand_status", "is_ambiguous", "VERDICT_ACTION", "VERDICT_RANK"]


def brand_status(candidate: dict, line: dict, aliases: list[str], learned: list[str]) -> dict:
    recorded = (candidate.get("brand") or "").strip()
    if not recorded:
        names = [line["brand"], *learned, *aliases, *line["other_names"]]
        if any(brand_in_text(n, candidate.get("product") or "") for n in names if n):
            return {"method": "in_name", "score": 1.0, "recorded_as": candidate.get("product") or ""}
        return {"method": "missing", "score": 0.0, "recorded_as": ""}
    n = norm(recorded)
    if n.replace(" ", "") == norm(line["brand"]).replace(" ", ""):
        return {"method": "exact", "score": 1.0, "recorded_as": recorded}
    for method, names in (("learned", learned), ("alias", aliases), ("notice", line["other_names"])):
        if any(n == norm(x) or n.replace(" ", "") == norm(x).replace(" ", "") for x in names):
            return {"method": method, "score": 1.0, "recorded_as": recorded}
    score = max(brand_similarity(recorded, x) for x in [line["brand"], *aliases, *line["other_names"]] if x)
    if score >= FUZZY_MIN:
        return {"method": "fuzzy", "score": score, "recorded_as": recorded}
    return {"method": "different", "score": score, "recorded_as": recorded}


def _upc_status(candidate: dict, line: dict) -> str:
    if not line["upc_keys"]:
        return "line_none"
    keys = upc_keys(candidate.get("upc"))
    if not keys:
        return "item_missing"
    return "match" if keys & set(line["upc_keys"]) else "mismatch"


def _verdict(verdict: str, conf: float, why: str, line: dict, checks: list, brand: dict, coverage: float | None) -> dict:
    return {"verdict": verdict, "confidence": conf, "rationale": why, "action": VERDICT_ACTION[verdict],
            "rule_checks": checks, "line_id": line["line_id"], "line_label": line["label"], "line_brand": line["brand"],
            "brand_match": brand, "name_coverage": coverage}


def _check_line(candidate: dict, line: dict, aliases: list[str], learned: list[str]) -> dict:
    label = line["label"]
    checks = []
    upc = _upc_status(candidate, line)
    brand = brand_status(candidate, line, aliases, learned)
    name = product_match(candidate.get("product") or "", line["terms"])

    if upc != "line_none":
        checks.append({"rule": "upc_match", "passed": {"match": True, "mismatch": False}.get(upc),
                       "note": {"match": f"UPC {candidate.get('upc')} is on the notice",
                                "mismatch": f"UPC {candidate.get('upc')} is not on the notice ({', '.join(line['upcs'])})",
                                "item_missing": "No UPC recorded on this item"}[upc]})
    if upc == "mismatch":
        return _verdict("NO_MATCH", 0.96, f"Different UPC from {label} on the notice, so a different product", line, checks, brand, None)

    checks.append({"rule": "brand", "passed": True if brand["method"] in STRONG_BRAND else None if brand["method"] in ("fuzzy", "missing", "alias") else False,
                   "note": {"exact": "Brand matches the notice", "learned": f"'{brand['recorded_as']}' was approved by a person in an earlier run",
                            "alias": f"'{brand['recorded_as']}' is a spelling Nemotron suggested for {line['brand']}; not approved by a person",
                            "notice": f"'{brand['recorded_as']}' is a name the notice itself prints",
                            "in_name": "No brand field, but the brand is written in the product name",
                            "fuzzy": f"'{brand['recorded_as']}' resembles {line['brand']} ({round(brand['score'] * 100)}% spelling match); not approved",
                            "missing": "No brand recorded", "different": f"Brand '{brand['recorded_as']}' is not {line['brand']}"}[brand["method"]]})
    if upc != "match":
        if brand["method"] == "different":
            return _verdict("NO_MATCH", 0.95, f"Different brand ('{brand['recorded_as']}'), not {line['brand']}", line, checks, brand, None)
        checks.append({"rule": "product", "passed": name["head_ok"],
                       "note": f"Name covers {round(name['coverage'] * 100)}% of '{line['product']}'"
                               + ("" if name["head_ok"] else f"; it is not {name['head']}")})
        if not name["head_ok"]:
            return _verdict("NO_MATCH", 0.93, f"Not the recalled product: the notice is for {label}", line, checks, brand, name["coverage"])
        other_size = size_conflict(candidate.get("product") or "", line)
        if other_size:
            checks.append({"rule": "size", "passed": False, "note": f"Item is {other_size}; the notice line is {line['size'] or line['product']}"})
            return _verdict("NO_MATCH", 0.9, f"Different pack size ({other_size}); the notice recalls {label}", line, checks, brand, name["coverage"])

    # The notice's own codes: lot list or ALL, and best-by dates
    parts = []
    if line["lots"]["kind"] != "none":
        lot = lot_status(candidate.get("lot_code"), line["lots"])
        checks.append({"rule": "lot", "passed": {"match": True, "nomatch": False}.get(lot["status"]), "note": lot["why"]})
        parts.append(("lot", lot))
    if line["dates"]["kind"] != "none":
        bb = date_status(candidate.get("best_by"), line["dates"])
        checks.append({"rule": "best_by", "passed": {"match": True, "nomatch": False}.get(bb["status"]), "note": bb["why"]})
        parts.append(("best_by", bb))
    states = [p["status"] for _, p in parts]

    if not parts:
        return _verdict("HUMAN_CHECK", 0.5, f"The notice recalls {label} but lists no lot codes or dates for it; confirm with the recalling firm",
                        line, checks, brand, name["coverage"])
    if "match" not in states and "nomatch" in states:
        dist = next((p.get("distance") for k, p in parts if k == "lot" and p.get("distance") is not None), None)
        why = "; ".join(p["why"] for _, p in parts if p["status"] == "nomatch")
        if dist is not None and dist <= NEAR_MISS_MAX:
            return _verdict("NEAR_MISS", 0.92, f"{why[0].upper() + why[1:]}, just outside the recall; leave in stock but watch for an expansion",
                            line, checks, brand, name["coverage"])
        return _verdict("NO_MATCH", 0.95, f"Same product, but {why}", line, checks, brand, name["coverage"])
    if "unreadable" in states:
        why = next(p["why"] for _, p in parts if p["status"] == "unreadable")
        return _verdict("HUMAN_CHECK", 0.55, f"{why[0].upper() + why[1:]}; check the physical case", line, checks, brand, name["coverage"])
    if "nomatch" in states:
        why = "; ".join(p["why"] for _, p in parts)
        return _verdict("HUMAN_CHECK", 0.6, f"The codes disagree: {why}; read the label", line, checks, brand, name["coverage"])
    if "missing" in states:
        have = "; ".join(p["why"] for _, p in parts if p["status"] == "match")
        miss = " and ".join(k.replace("_", "-") for k, p in parts if p["status"] == "missing")
        lead = f"{have[0].upper() + have[1:]}, but" if have else "Looks like the recalled product, but"
        return _verdict("HUMAN_CHECK", 0.6, f"{lead} the {miss} was not recorded; read it on the package and compare with {label}",
                        line, checks, brand, name["coverage"])

    # Every code the notice gives matches. How sure is the identity?
    codes = "; ".join(p["why"] for _, p in parts)
    if upc == "match":
        return _verdict("CONFIRMED_MATCH", 0.98, f"UPC matches {label} and {codes}", line, checks, brand, name["coverage"])
    strong = brand["method"] in STRONG_BRAND
    specific_lot = any(k == "lot" and line["lots"]["kind"] == "list" for k, _ in parts)
    if upc == "line_none" and strong:
        return _verdict("CONFIRMED_MATCH", 0.92, f"Brand and product match {label}, and {codes}", line, checks, brand, name["coverage"])
    if strong and specific_lot and name["coverage"] >= NAME_COVERAGE_MIN:
        return _verdict("CONFIRMED_MATCH", 0.88, f"No UPC recorded, but brand, product name and the specific lot code match {label} ({codes})",
                        line, checks, brand, name["coverage"])
    # A specific lot code and the best-by date of the same notice line agreeing is evidence enough without a UPC
    if strong and specific_lot and len(parts) == 2:
        return _verdict("CONFIRMED_MATCH", 0.86, f"No UPC recorded, but brand, lot code and best-by date all match {label} ({codes})",
                        line, checks, brand, name["coverage"])
    gap = ("the brand only resembles the notice's" if brand["method"] == "fuzzy" else
           "the brand spelling was only suggested by the model" if brand["method"] == "alias" else
           "no brand was recorded" if brand["method"] == "missing" else
           "the name only partly matches" if name["coverage"] < NAME_COVERAGE_MIN else
           "a date or 'all lots' is not specific enough without a UPC")
    return _verdict("PROBABLE_MATCH", 0.72, f"Probably {label} ({codes}), but {gap}; confirm the UPC on the package", line, checks, brand, name["coverage"])


def evaluate(criteria: dict, candidate: dict) -> dict:
    """The most serious verdict across the recall's product lines; each line is judged on its own codes."""
    best = None
    for line in criteria["lines"]:
        aliases = criteria.get("aliases", {}).get(line["brand"], [])
        learned = criteria.get("learned", {}).get(line["brand"], [])
        v = _check_line(candidate, line, aliases, learned)
        if best is None or VERDICT_RANK[v["verdict"]] > VERDICT_RANK[best["verdict"]]:
            best = v
    return {"item_id": candidate["item_id"], **best}
