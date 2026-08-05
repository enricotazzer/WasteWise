"""Scan API.

Covers the HTTP contract the phone talks to: an unknown barcode must be a 200 with an
"ask the user" flag, never a 4xx the UI has to special-case.
"""

import httpx
import respx
from factories import off_response
from fastapi.testclient import TestClient

from app.config import settings

BARCODE = "8076809513692"
URL = f"{settings.off_base_url}/api/v2/product/{BARCODE}.json"


def _mock_off(**kwargs):
    return respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(**kwargs)))


@respx.mock
def test_scan_resolves_a_known_product(auth_client: TestClient, seeded, no_llm):
    _mock_off()
    body = auth_client.get(f"/api/scan/{BARCODE}").json()
    assert body["match_method"] == "off"
    assert body["product_name"] == "Napoletana"
    assert body["category_code"] == "PASSATA"


@respx.mock
def test_an_unknown_barcode_is_200_with_a_flag_not_a_404(auth_client: TestClient, seeded, no_llm):
    """A 404 would make "we don't know this yet" indistinguishable from "the route is
    wrong", and the scanner would have to treat a normal outcome as an error."""
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(found=False)))
    resp = auth_client.get(f"/api/scan/{BARCODE}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product_id"] is None
    assert body["needs_confirmation"] is True


def test_a_barcode_with_no_digits_is_422(auth_client: TestClient, seeded, no_llm):
    assert auth_client.get("/api/scan/abc").status_code == 422


def test_scan_requires_authentication(client: TestClient):
    assert client.get(f"/api/scan/{BARCODE}").status_code == 401
    assert client.get("/api/scan/stats").status_code == 401


def test_bind_an_unknown_barcode_to_a_new_product(auth_client: TestClient, seeded, no_llm):
    resp = auth_client.post(
        "/api/scan/2100123456789/bind",
        json={
            "new_product_name": "Mele Golden sfuse",
            "category_id": seeded["fridge"].id,
            "shelf_life_days": 21,
            "default_unit": "kg",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["match_method"] == "manual"

    again = auth_client.get("/api/scan/2100123456789").json()
    assert again["match_method"] == "cache"
    assert again["product_name"] == "Mele Golden sfuse"


def test_bind_without_enough_information_is_422(auth_client: TestClient, seeded, no_llm):
    assert auth_client.post("/api/scan/2100123456789/bind", json={}).status_code == 422


def test_bind_to_a_missing_product_is_404(auth_client: TestClient, seeded, no_llm):
    resp = auth_client.post("/api/scan/2100123456789/bind", json={"product_id": 9999})
    assert resp.status_code == 404


def test_add_from_scan_records_the_barcode_on_the_item(auth_client: TestClient, seeded, no_llm):
    """The barcode is how a scanned item is told apart from a hand-added one later, when
    the hit rate gets computed over real use."""
    resp = auth_client.post(
        "/api/scan/add",
        json={"barcode": BARCODE, "product_id": seeded["mozzarella"].id, "quantity": 1},
    )
    assert resp.status_code == 201
    assert resp.json()["barcode"] == BARCODE

    listed = auth_client.get("/api/inventory/items").json()
    assert listed[0]["barcode"] == BARCODE


def test_a_price_given_at_scan_time_becomes_a_price_observation(
    auth_client: TestClient, db, seeded, no_llm
):
    """Prices are optional and hand-typed, so each one is the only price signal the system
    gets — it has to outlive the item it came in on."""
    from app.models import PriceObservation

    auth_client.post(
        "/api/scan/add",
        json={
            "barcode": BARCODE,
            "product_id": seeded["mozzarella"].id,
            "quantity": 1,
            "unit_price_eur": 1.29,
            "store": "Esselunga",
        },
    )
    obs = db.query(PriceObservation).one()
    assert float(obs.unit_price_eur) == 1.29
    assert obs.store == "Esselunga"
    assert obs.inventory_item_id is not None


def test_adding_without_a_price_writes_no_observation(auth_client: TestClient, db, seeded, no_llm):
    from app.models import PriceObservation

    auth_client.post("/api/scan/add", json={"product_id": seeded["mozzarella"].id, "quantity": 1})
    assert db.query(PriceObservation).count() == 0


def test_add_from_scan_honours_a_printed_expiry_date(auth_client: TestClient, seeded, no_llm):
    resp = auth_client.post(
        "/api/scan/add",
        json={
            "product_id": seeded["mozzarella"].id,
            "quantity": 1,
            "expiry_date": "2026-09-01",
        },
    )
    assert resp.json()["expiry_date"] == "2026-09-01"
    assert resp.json()["expiry_source"] == "label"


def test_a_malformed_expiry_date_is_422(auth_client: TestClient, seeded, no_llm):
    resp = auth_client.post(
        "/api/scan/add",
        json={"product_id": seeded["mozzarella"].id, "quantity": 1, "expiry_date": "01/09/2026"},
    )
    assert resp.status_code == 422


def test_add_from_scan_for_an_unknown_product_is_404(auth_client: TestClient, seeded, no_llm):
    resp = auth_client.post("/api/scan/add", json={"product_id": 9999, "quantity": 1})
    assert resp.status_code == 404


# --- the Module 1 scorecard ---


def test_scan_stats_are_empty_before_any_scanning(auth_client: TestClient, seeded, no_llm):
    stats = auth_client.get("/api/scan/stats").json()
    assert stats["total_barcodes"] == 0
    # No denominator yet — None rather than a misleading 0.0 or 1.0.
    assert stats["zero_typing_rate"] is None


@respx.mock
def test_zero_typing_rate_counts_scans_not_barcodes(auth_client: TestClient, seeded, no_llm):
    """Two scans of one product are two units of saved effort, not one — the metric is about
    the user's time, so the denominator has to be scans."""
    _mock_off()
    auth_client.get(f"/api/scan/{BARCODE}")  # off
    auth_client.get(f"/api/scan/{BARCODE}")  # cache
    auth_client.post("/api/scan/2100123456789/bind", json={"product_id": seeded["pasta"].id})

    stats = auth_client.get("/api/scan/stats").json()
    assert stats["by_method"] == {"off": 1, "manual": 1}
    assert stats["total_scans"] == 3  # 2 free + 1 typed
    assert stats["zero_typing_rate"] == 2 / 3


@respx.mock
def test_naming_is_charged_once_not_on_every_later_scan(auth_client: TestClient, seeded, no_llm):
    """The bind-once loop is the whole design: type a name one time, and that barcode is
    free forever after. If each later scan were charged as 'typed', the rate would *fall*
    as the catalog filled in — the opposite of what it's meant to show."""
    auth_client.post("/api/scan/2100123456789/bind", json={"product_id": seeded["pasta"].id})
    for _ in range(4):
        auth_client.get("/api/scan/2100123456789")  # cache hits on a hand-bound code

    stats = auth_client.get("/api/scan/stats").json()
    assert stats["total_scans"] == 5
    # One naming event, four free scans.
    assert stats["zero_typing_rate"] == 4 / 5


@respx.mock
def test_unresolved_scans_count_against_the_rate_every_time(
    auth_client: TestClient, seeded, no_llm
):
    """Unlike a bind, an unresolved scan produced nothing — it prompted for a name and was
    skipped. Each repeat is a fresh miss, so it is charged in full."""
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(found=False)))
    auth_client.get(f"/api/scan/{BARCODE}")
    stats = auth_client.get("/api/scan/stats").json()
    assert stats["unresolved"] == 1
    assert stats["resolved"] == 0
    assert stats["zero_typing_rate"] == 0.0

    auth_client.get(f"/api/scan/{BARCODE}")
    assert auth_client.get("/api/scan/stats").json()["zero_typing_rate"] == 0.0
