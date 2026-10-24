from fastapi.testclient import TestClient

from api.main import app
from zetaris.client import default_source_map, list_agencies, search_shipments, search_stock

client = TestClient(app)


def test_stock_search_finds_a_misspelled_brand_with_quantity_on_hand():
    rows = search_stock(default_source_map(), "mezzeta")
    assert [r["item_id"] for r in rows] == ["INV-0115"]
    assert rows[0]["location"] == "C6"
    assert rows[0]["quantity_on_hand"] == 6.0
    assert rows[0]["source_ref"].startswith("inventory.csv line")


def test_stock_search_matches_a_upc_fragment():
    rows = search_stock(default_source_map(), "072250916622")
    assert rows and all("072250916622" in (r["upc"] or "") for r in rows)


def test_shipments_join_agency_and_product():
    rows = search_shipments(default_source_map(), "eastvale")
    assert rows and all(r["agency_name"] == "Eastvale Neighbors Pantry" for r in rows)
    assert all(r["product"] for r in rows)
    dates = [r["ship_date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_browse_routes_return_rows_and_reject_long_queries():
    assert len(client.get("/api/stock").json()["rows"]) == 180
    assert client.get("/api/shipments", params={"q": "eastvale"}).json()["rows"]
    assert client.get("/api/stock", params={"q": "x" * 81}).status_code == 400


def test_brand_assets_are_served():
    assert client.get("/assets/favicon-32.png").status_code == 200


def test_agencies_list_contacts_with_shipment_counts():
    rows = list_agencies(default_source_map())
    assert len(rows) == 16
    eastvale = next(r for r in rows if r["name"] == "Eastvale Neighbors Pantry")
    assert eastvale["shipments"] == 17 and eastvale["contact_email"].endswith(".example")
    assert sum(r["shipments"] for r in rows) == 115
    assert client.get("/api/agencies").json()["rows"]
