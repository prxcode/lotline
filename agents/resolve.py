import re
from difflib import SequenceMatcher

# A spelling below this score is not searched at all
FUZZY_MIN = 0.75

_SUFFIXES = {"inc", "llc", "co", "corp", "ltd", "company", "brand"}


def tokens(name: str) -> list[str]:
    # "Nature's" and "Natures" are the same word
    text = re.sub(r"['’]", "", (name or "").lower())
    return [w for w in re.findall(r"[a-z0-9]+", text) if w not in _SUFFIXES]


def norm(name: str) -> str:
    return " ".join(tokens(name))


def _explain(tok: str, target: list[str], start: int) -> tuple[int, int] | None:
    """Find target words from `start` on that `tok` stands for. Returns (next index, words covered)."""
    for i in range(start, len(target)):
        if tok == target[i] or (len(tok) >= 3 and target[i].startswith(tok)):
            return i + 1, 1
        for n in range(len(target) - i, 1, -1):
            run = target[i:i + n]
            # "hc" = Hollow Creek, "hollowcreek" = Hollow Creek
            if tok in ("".join(w[0] for w in run), "".join(run)):
                return i + n, n
    return None


def brand_similarity(candidate: str, target: str) -> float:
    """How likely `candidate` is a way of writing `target`, from 0 to 1."""
    c, t = tokens(candidate), tokens(target)
    if not c or not t:
        return 0.0
    if c == t or "".join(c) == "".join(t):
        return 1.0
    pos = covered = 0
    for tok in c:
        hit = _explain(tok, t, pos)
        if hit is None:
            break
        pos, n = hit
        covered += n
    else:
        return round(0.55 + 0.4 * covered / len(t), 3)
    ratio = SequenceMatcher(None, " ".join(c), " ".join(t)).ratio()
    return round(ratio * 0.9, 3) if ratio >= 0.88 else 0.0


def resolve_brands(target: str, aliases: list[str], learned: list[str], known_brands: list[str]) -> dict[str, dict]:
    """Map every brand string in the data that could be `target` to how it matched."""
    learned_n = {norm(a) for a in learned}
    alias_n = {norm(a) for a in aliases}
    out: dict[str, dict] = {}
    for brand in known_brands:
        n = norm(brand)
        if not n:
            continue
        if n == norm(target) or n.replace(" ", "") == norm(target).replace(" ", ""):
            out[brand] = {"method": "exact", "score": 1.0}
        elif n in learned_n:
            out[brand] = {"method": "learned", "score": 1.0}
        elif n in alias_n:
            out[brand] = {"method": "alias", "score": 0.95}
        else:
            score = max(brand_similarity(brand, x) for x in [target, *aliases])
            if score >= FUZZY_MIN:
                out[brand] = {"method": "fuzzy", "score": score}
    return out
