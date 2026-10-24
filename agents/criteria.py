import re
from datetime import date
from difflib import SequenceMatcher
from agents.resolve import tokens as brand_tokens

_STOP = {"and", "or", "the", "of", "in", "with", "oz", "lb", "lbs", "ct", "count", "pack", "pk", "bag", "bags", "box",
         "sack", "jar", "can", "cans", "size", "item", "single", "loaf", "shipper", "ftc", "srf", "a", "an", "for"}
# Words that describe a product without naming it; worth half a naming word
_DESCRIPTORS = {"organic", "frozen", "iqf", "sliced", "shredded", "whole", "crushed", "diced", "fresh", "natural",
                "original", "classic", "homestyle", "authentic", "gourmet", "dark", "light", "medium", "mild", "hot",
                "golden", "extra", "virgin", "premium", "easter", "sweet", "bulk"}


# UPC and GTIN

def _check_ok(d: str) -> bool:
    body, check = d[:-1], int(d[-1])
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def upc_keys(code) -> set[str]:
    """Comparable cores of a printed UPC/GTIN; notices drop leading zeros or the check digit."""
    d = re.sub(r"\D", "", str(code or ""))
    if len(d) < 6:
        return set()
    keys = {d.lstrip("0")}
    if len(d) >= 8 and _check_ok(d):
        keys.add(d[:-1].lstrip("0"))
        if len(d) == 14:
            keys.add(d[1:-1].lstrip("0"))
    return {k for k in keys if len(k) >= 6}


def split_list(cell) -> list[str]:
    return [p.strip() for p in re.split(r"[;\n]", str(cell or "")) if p.strip()]


# Lot and product codes

def _norm_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9?]", "", str(code or "").upper())


def is_ambiguous(lot_code: str) -> bool:
    # "241X" or "241?" is a smudged digit; the X in "LOT-XB-12" is just a letter
    return bool(re.search(r"\?|(?<=\d)X|X(?=\d)", lot_code or "", re.IGNORECASE))


def _split_num(code: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)(\d+)$", code)
    return (m.group(1), m.group(2)) if m else (code, "")


def parse_lots(cell) -> dict:
    text = str(cell or "").strip()
    if not text:
        return {"kind": "none", "specs": [], "source": ""}
    if text.upper() in ("ALL", "ALL LOTS"):
        return {"kind": "all", "specs": [], "source": text}
    specs = []
    for part in split_list(text):
        ends = re.split(r"\s+[-–]\s+", part)
        if len(ends) == 2:
            specs.append({"kind": "range", "start": _norm_code(ends[0]), "end": _norm_code(ends[1]), "text": part})
        else:
            specs.append({"kind": "exact", "value": _norm_code(part), "text": part})
    return {"kind": "list", "specs": specs, "source": text}


def _in_spec(value: str, spec: dict) -> bool:
    if spec["kind"] == "exact":
        return value == spec["value"]
    (p, n), (ps, s), (pe, e) = _split_num(value), _split_num(spec["start"]), _split_num(spec["end"])
    if not (n and s and e) or p != ps or ps != pe or len(n) != len(s):
        return False
    return int(s) <= int(n) <= int(e)


def _distance(value: str, spec: dict) -> int | None:
    p, n = _split_num(value)
    bounds = [spec["value"]] if spec["kind"] == "exact" else [spec["start"], spec["end"]]
    out = None
    for b in bounds:
        pb, nb = _split_num(b)
        if n and nb and p == pb and len(n) == len(nb):
            d = abs(int(n) - int(nb))
            out = d if out is None else min(out, d)
    return out


def lot_status(item_lot, lots: dict) -> dict:
    """match / nomatch / unreadable / missing, with the spec that decided it."""
    raw = str(item_lot or "").strip()
    if lots["kind"] == "all":
        return {"status": "match", "why": "the notice recalls all lots"}
    if not raw:
        return {"status": "missing", "why": "no lot code recorded"}
    value = _norm_code(re.sub(r"(?<=\d)X|X(?=\d)", "?", raw, flags=re.IGNORECASE))
    if "?" in value:
        holes = value.count("?")
        options = [value] if holes > 2 else [value.replace("?", "{}").format(*f"{i:0{holes}d}") for i in range(10 ** holes)]
        hits = [s for s in lots["specs"] for o in options if _in_spec(o, s)]
        if hits:
            return {"status": "unreadable", "why": f"lot '{raw}' is partly unreadable and could be {hits[0]['text']}"}
        return {"status": "nomatch", "why": f"lot '{raw}' is partly unreadable but cannot be a recalled code"}
    for s in lots["specs"]:
        if _in_spec(value, s):
            return {"status": "match", "why": f"lot {raw} is in {s['text']}"}
    dists = [d for d in (_distance(value, s) for s in lots["specs"]) if d is not None]
    return {"status": "nomatch", "why": f"lot {raw} is not a recalled code", "distance": min(dists) if dists else None}


# Best-by dates

def _date(text) -> date | None:
    t = str(text or "").strip()
    for pat, order in ((r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"), (r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", "mdy")):
        m = re.match(pat, t)
        if m:
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else ((c + 2000 if c < 100 else c), a, b)
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def parse_dates(cell) -> dict:
    specs = []
    for part in split_list(cell):
        ends = re.split(r"\s+[-–]\s+", part)
        start, end = _date(ends[0]), _date(ends[-1])
        if start and end:
            specs.append({"start": start, "end": end, "text": part})
    return {"kind": "list" if specs else "none", "specs": specs, "source": str(cell or "")}


def date_status(item_date, dates: dict) -> dict:
    d = _date(item_date)
    if not str(item_date or "").strip():
        return {"status": "missing", "why": "no best-by date recorded"}
    if not d:
        return {"status": "unreadable", "why": f"best-by '{item_date}' cannot be read"}
    for s in dates["specs"]:
        if s["start"] <= d <= s["end"]:
            span = f" ({s['text']})" if s["start"] != s["end"] else ""
            return {"status": "match", "why": f"best-by {d.isoformat()} is a recalled date{span}"}
    return {"status": "nomatch", "why": f"best-by {d.isoformat()} is not a recalled date"}


# Product names

def _words(text: str) -> list[str]:
    text = re.sub(r"['’]", "", (text or "").lower())
    return [w for w in re.findall(r"[a-z]+", text) if w not in _STOP and len(w) > 1]


def product_terms(description: str, brand: str = "") -> dict:
    """Weighted naming words of a product line, and its head noun (what the product is)."""
    plain = re.sub(r"\([^)]*\)", " ", description or "")
    head_part = re.split(r",|\s+in\s+|\s+with\s+|/", plain, maxsplit=1)[0]
    skip = set(brand_tokens(brand))
    words = [w for w in _words(plain) if w not in skip]
    head = next((w for w in reversed([w for w in _words(head_part) if w not in skip]) if w not in _DESCRIPTORS), None)
    return {"words": list(dict.fromkeys(words)), "head": head}


def _covers(line_word: str, item_words: list[str]) -> bool:
    for w in item_words:
        if w == line_word:
            return True
        if len(w) >= 3 and line_word.startswith(w):
            return True
        if len(line_word) >= 3 and w.startswith(line_word) and len(w) - len(line_word) <= 2:
            return True
        if len(w) >= 4 and SequenceMatcher(None, w, line_word).ratio() >= 0.8:
            return True
    return False


def product_match(item_text: str, terms: dict) -> dict:
    item_words = _words(item_text)
    # "applesauce" stands for "apple sauce"
    covered_by_join = set()
    for a, b in zip(terms["words"], terms["words"][1:]):
        if a + b in item_words:
            covered_by_join |= {a, b}
    total = got = 0.0
    for w in terms["words"]:
        weight = 0.5 if w in _DESCRIPTORS else 1.0
        total += weight
        if w in covered_by_join or _covers(w, item_words):
            got += weight
    head = terms["head"]
    head_ok = bool(head) and (head in covered_by_join or _covers(head, item_words))
    return {"coverage": round(got / total, 2) if total else 0.0, "head_ok": head_ok, "head": head}


_SIZE = re.compile(r"(\d+(?:\.\d+)?|\.\d+)\s*(oz|ounces?|lbs?|pounds?|kg|g)\b", re.IGNORECASE)
_TO_OZ = {"oz": 1.0, "ounce": 1.0, "ounces": 1.0, "lb": 16.0, "lbs": 16.0, "pound": 16.0, "pounds": 16.0, "kg": 35.274, "g": 0.035274}


def sizes_oz(text: str) -> set[float]:
    return {round(float(n) * _TO_OZ[u.lower()], 1) for n, u in _SIZE.findall(text or "")}


def size_conflict(item_text: str, line: dict) -> str | None:
    """The size the item states, if it differs from every size on the notice line."""
    have = sizes_oz(item_text)
    want = sizes_oz(f"{line['size']} {line['product']}")
    if not have or not want:
        return None
    if any(abs(h - w) <= 0.02 * w for h in have for w in want):
        return None
    return ", ".join(m.group(0) for m in _SIZE.finditer(item_text))


def search_stem(word: str) -> str:
    # Short enough that "blueberrys" and "applesauce" still contain it
    return word[:max(3, len(word) - 3)] if len(word) > 4 else word


def brand_in_text(brand: str, text: str) -> bool:
    b = brand_tokens(brand)
    t = brand_tokens(text)
    if not b or not t:
        return False
    target = "".join(b)
    for size in (len(b), 1):
        for i in range(len(t) - size + 1):
            if SequenceMatcher(None, "".join(t[i:i + size]), target).ratio() >= 0.9:
                return True
    return False


def parse_line(row: dict) -> dict:
    """Typed criteria for one recalled product line. Every field keeps the notice text it came from."""
    upcs = split_list(row.get("upc"))
    return {
        "line_id": str(row.get("line_id") or "1"),
        "brand": (row.get("brand") or "").strip(),
        "product": (row.get("product") or "").strip(),
        "size": (row.get("size") or "").strip(),
        "label": " ".join(x for x in [(row.get("product") or "").strip(), (row.get("size") or "").strip()] if x),
        "upcs": upcs,
        "upc_keys": sorted(set().union(*[upc_keys(u) for u in upcs])) if upcs else [],
        "lots": parse_lots(row.get("lot_codes")),
        "dates": parse_dates(row.get("best_by")),
        "terms": product_terms(row.get("product") or "", row.get("brand") or ""),
        "other_names": split_list(row.get("other_names")),
    }
