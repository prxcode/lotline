from agents.criteria import parse_line, upc_keys, lot_status, parse_lots, date_status, parse_dates, is_ambiguous
from agents.rules import evaluate
from agents.resolve import brand_similarity, resolve_brands, FUZZY_MIN
from agents.reporter import _check_briefing
from zetaris.schema import detect_role, validate

# Product lines as printed in FDA notices (data/recall_products.csv)
NATURES_OWN = parse_line({"brand": "Nature's Own", "product": "Hawaiian Bread", "size": "20 oz", "upc": "072250916622",
                          "lot_codes": "0188 262 20:06 - 0188 262 22:05; 0188 265 09:20 - 0188 265 10:30",
                          "best_by": "2026-10-08 - 2026-10-18"})
SKINNYDIPPED = parse_line({"brand": "SkinnyDipped", "product": "Dark Chocolate Coconut Almond Bites 10ct", "size": "3.17 oz",
                           "upc": "10850034219780; 850034219783; 850034219776", "lot_codes": "B15356; B15357; B35360",
                           "best_by": "2026-12-20; 2026-12-23; 2026-12-26"})
FALCON = parse_line({"brand": "Falcon Trading Company", "product": "Black Beans Organic (FTC item 003040)", "size": "25 lb sack",
                     "upc": "086700930403", "lot_codes": "ALL", "other_names": "SRF"})
MEZZETTA = parse_line({"brand": "Mezzetta", "product": "Golden Greek Peperoncini, Medium Heat", "size": "32 oz", "lot_codes": "720106"})
GENOVA = parse_line({"brand": "Genova", "product": "Yellowfin Tuna in Olive Oil 4 Pack", "size": "5.0 oz", "upc": "4800073265",
                     "lot_codes": "S84N D2L", "best_by": "2028-01-21"})
KROGER = parse_line({"brand": "Kroger", "product": "Homestyle Cheese Garlic Croutons", "size": "5 oz", "upc": "011110813534",
                     "best_by": "2027-02-17; 2027-02-18; 2027-04-07"})


def item(**kw):
    return {"item_id": "X", "brand": "", "product": "", "upc": "", "lot_code": "", "best_by": "", **kw}


def verdict(line, **kw):
    return evaluate({"lines": [line]}, item(**kw))["verdict"]


def test_upc_as_printed():
    # Notices drop leading zeros or the check digit; cases carry a GTIN-14
    assert upc_keys("0 72250 91662 2") & upc_keys("072250916622")
    assert upc_keys("4800073265") & upc_keys("048000732651")
    assert upc_keys("10850034219780") & upc_keys("850034219783")
    assert not upc_keys("015881399860") & upc_keys("072250916622")


def test_timestamped_product_codes():
    lots = parse_lots("0188 262 20:06 - 0188 262 22:05; 0188 265 09:20 - 0188 265 10:30")
    assert lot_status("0188 262 21:15", lots)["status"] == "match"
    assert lot_status("0188265 09:45", lots)["status"] == "match"
    assert lot_status("0188 268 11:30", lots)["status"] == "nomatch"


def test_lot_lists_and_written_variants():
    assert lot_status("B-35360", parse_lots("B15356; B35360"))["status"] == "match"
    assert lot_status("60-401", parse_lots("60401"))["status"] == "match"
    assert lot_status("anything", parse_lots("ALL"))["status"] == "match"


def test_smudged_codes():
    assert is_ambiguous("241X") and is_ambiguous("241?") and not is_ambiguous("LOT-XB-12")
    assert lot_status("241X", parse_lots("2410 - 2415"))["status"] == "unreadable"
    assert lot_status("999X", parse_lots("2410 - 2415"))["status"] == "nomatch"


def test_best_by_lists_are_not_ranges():
    dates = parse_dates("2027-02-17; 2027-02-18; 2027-04-07")
    assert date_status("2027-02-18", dates)["status"] == "match"
    assert date_status("2027-03-01", dates)["status"] == "nomatch"
    assert date_status("", dates)["status"] == "missing"


def test_upc_and_codes_confirm():
    assert verdict(NATURES_OWN, brand="Nature's Own", product="Hawaiian Bread 20 oz", upc="072250916622",
                   lot_code="0188 262 21:15", best_by="2026-10-10") == "CONFIRMED_MATCH"
    assert verdict(KROGER, brand="Kroger", product="Kroger Homestyle Cheese Garlic Croutons 5 oz", upc="011110813534",
                   best_by="2027-02-18") == "CONFIRMED_MATCH"


def test_other_product_of_same_brand_is_cleared():
    assert verdict(NATURES_OWN, brand="Nature's Own", product="Honey Wheat Bread 20 oz", upc="015881399860",
                   lot_code="0188 262 21:15", best_by="2026-10-10") == "NO_MATCH"
    assert verdict(MEZZETTA, brand="Mezzetta", product="Mezzetta Golden Greek Peperoncini Mild 16 oz", lot_code="720106") == "NO_MATCH"


def test_missing_code_goes_to_a_person():
    assert verdict(NATURES_OWN, brand="Nature's Own", product="hawaiian sweet bread", upc="072250916622",
                   best_by="2026-10-13") == "HUMAN_CHECK"


def test_no_upc_confirmed_only_by_specific_evidence():
    # Misspelled name, no UPC, but the specific code and date both match
    assert verdict(SKINNYDIPPED, brand="Skinny Dipped", product="skinnydiped choc coconut almond bites 10 ct",
                   lot_code="B-35360", best_by="2026-12-26") == "CONFIRMED_MATCH"
    assert verdict(GENOVA, brand="Genova", product="genova yellowfin tuna olive oil 4pk", lot_code="S84N-D2L",
                   best_by="2028-01-21") == "CONFIRMED_MATCH"
    # "All lots" is not specific: without a UPC a person confirms it
    assert verdict(FALCON, brand="SRF", product="black beans organic 25 lb sack") == "PROBABLE_MATCH"
    assert verdict(FALCON, brand="", product="black beans 25 lb sack (bulk)") == "PROBABLE_MATCH"


def test_fuzzy_brand_is_never_confirmed_without_upc():
    assert verdict(MEZZETTA, brand="Mezzeta", product="mezzeta greek peperonchini med heat 32z", lot_code="72-0106") == "PROBABLE_MATCH"


def test_brand_spellings():
    target = "Hollow Creek Farms"
    for spelling in ("HC Farms", "HCF", "HollowCreekFarms", "Hollow Crek Farms", "Hollow Creek"):
        assert brand_similarity(spelling, target) >= FUZZY_MIN, spelling
    for other in ("Farms", "Sunfield Pantry", "Riverbend Bakery"):
        assert brand_similarity(other, target) < FUZZY_MIN, other
    assert brand_similarity("Green Wise", "GreenWise") == 1.0
    assert brand_similarity("Natures Own", "Nature's Own") == 1.0


def test_resolve_prefers_learned_over_fuzzy():
    got = resolve_brands("Hollow Creek Farms", [], ["HC Farms"], ["HC Farms", "Hollow Creek Farms", "Sunfield Pantry"])
    assert got["HC Farms"]["method"] == "learned" and got["Hollow Creek Farms"]["method"] == "exact"
    assert "Sunfield Pantry" not in got


def test_columns_are_mapped_from_names():
    role, mapping = detect_role(["item_id", "received_date", "donor_or_supplier", "product_name_as_entered", "brand_as_entered",
                                 "upc", "lot_code", "best_by", "quantity_received", "unit", "warehouse_location", "notes"])
    assert role == "stock" and mapping["brand"] == "brand_as_entered" and mapping["location"] == "warehouse_location"
    assert detect_role(["distribution_id", "date", "item_id", "agency_id", "quantity", "unit", "notes"])[0] == "shipments"
    # A second system's naming maps to the same fields
    role, mapping = detect_role(["item_id", "brand", "product", "lot_code", "upc", "quantity", "unit", "location", "source_type"])
    assert role == "stock" and mapping["quantity_on_hand"] == "quantity" and mapping["location"] == "location"
    assert detect_role(["dist_id", "item_id", "agency_id", "quantity", "distributed_date"])[0] == "shipments"
    assert validate("stock", ["item_id", "product"], {"item_id": "item_id", "product": "nope"})


def test_briefing_with_invented_number_is_rejected():
    facts = {"units_to_pull_now_in_warehouse": 56.0, "shipments_already_sent": 5}
    assert _check_briefing("Pull 56 units now; 5 shipments went out.", facts, "codes 2410 to 2415") is None
    assert "40" in _check_briefing("Pull 40 units now.", facts, "codes 2410 to 2415")


def test_model_suggested_spelling_never_confirms_without_upc():
    # Nemotron suggesting "HC Farms" must not be enough to pull a case with no UPC
    ham = parse_line({"brand": "Hollow Creek Farms", "product": "Sliced Deli Ham", "size": "12oz", "upc": "049800021340",
                      "lot_codes": "2410 - 2415"})
    crit = {"lines": [ham], "aliases": {"Hollow Creek Farms": ["HC Farms"]}}
    v = evaluate(crit, item(brand="HC Farms", product="Deli Ham Sliced", lot_code="2414"))
    assert v["verdict"] == "PROBABLE_MATCH" and v["brand_match"]["method"] == "alias"
    approved = evaluate({**crit, "learned": {"Hollow Creek Farms": ["HC Farms"]}}, item(brand="HC Farms", product="Deli Ham Sliced", lot_code="2414"))
    assert approved["verdict"] == "CONFIRMED_MATCH"
