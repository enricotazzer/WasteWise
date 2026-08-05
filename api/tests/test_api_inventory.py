"""Inventory lifecycle.

The assertions here protect the data that Phase 3 trains on: an item must resolve exactly
once, resolution must be recorded as an *event* (not a mutation), and the money figures
must exclude non-food.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient


def _create(client: TestClient, product_id: int, **kwargs) -> dict:
    payload = {"product_id": product_id, "quantity": 1, **kwargs}
    resp = client.post("/api/inventory/items", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_item_infers_expiry_from_product(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id, unit_price_eur=1.29)
    assert item["expiry_source"] == "product"
    assert item["expiry_date"] == (date.today() + timedelta(days=5)).isoformat()
    assert item["status"] == "active"
    assert item["fraction_remaining"] == 1.0


def test_create_item_falls_back_to_category(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["ricotta"].id)
    assert item["expiry_source"] == "category"


def test_explicit_label_date_is_respected(auth_client: TestClient, seeded: dict) -> None:
    label = (date.today() + timedelta(days=2)).isoformat()
    item = _create(auth_client, seeded["mozzarella"].id, expiry_date=label)
    assert item["expiry_date"] == label
    assert item["expiry_source"] == "label"
    assert item["days_to_expiry"] == 2


def test_unknown_product_is_404(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/inventory/items", json={"product_id": 9999, "quantity": 1})
    assert resp.status_code == 404


def test_zero_quantity_is_rejected(auth_client: TestClient, seeded: dict) -> None:
    resp = auth_client.post(
        "/api/inventory/items", json={"product_id": seeded["mozzarella"].id, "quantity": 0}
    )
    assert resp.status_code == 422


def test_items_are_listed_by_soonest_expiry_with_undated_last(
    auth_client: TestClient, seeded: dict
) -> None:
    _create(auth_client, seeded["pasta"].id)  # 540 days out
    _create(auth_client, seeded["mozzarella"].id)  # 5 days out
    _create(auth_client, seeded["detersivo"].id)  # no expiry at all

    names = [i["product_name"] for i in auth_client.get("/api/inventory/items").json()]
    assert names == ["Mozzarella", "Pasta", "Detersivo"]


def test_consumed_event_resolves_the_item(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id)
    resp = auth_client.post(
        f"/api/inventory/items/{item['id']}/events", json={"event_type": "consumed"}
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "manual"

    listed = auth_client.get("/api/inventory/items?active_only=false").json()
    assert listed[0]["status"] == "consumed"
    assert listed[0]["fraction_remaining"] == 0.0


def test_partial_events_accumulate_then_auto_resolve(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id)
    url = f"/api/inventory/items/{item['id']}/events"

    auth_client.post(url, json={"event_type": "partial", "fraction_used": 0.5})
    mid = auth_client.get("/api/inventory/items").json()[0]
    assert mid["status"] == "partial"
    assert mid["fraction_remaining"] == 0.5
    # A partial event implies the package is open.
    assert mid["opened_at"] is not None

    auth_client.post(url, json={"event_type": "partial", "fraction_used": 0.5})
    done = auth_client.get("/api/inventory/items?active_only=false").json()[0]
    assert done["status"] == "consumed"
    assert done["fraction_remaining"] == 0.0


def test_an_item_cannot_be_resolved_twice(auth_client: TestClient, seeded: dict) -> None:
    """Guards the training labels: a second resolution would double-count the outcome."""
    item = _create(auth_client, seeded["mozzarella"].id)
    url = f"/api/inventory/items/{item['id']}/events"
    assert auth_client.post(url, json={"event_type": "consumed"}).status_code == 201
    assert auth_client.post(url, json={"event_type": "wasted"}).status_code == 409


def test_waste_event_records_a_reason(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id)
    resp = auth_client.post(
        f"/api/inventory/items/{item['id']}/events",
        json={"event_type": "wasted", "reason": "dimenticato"},
    )
    assert resp.status_code == 201
    assert resp.json()["reason"] == "dimenticato"


def test_opening_an_item_shortens_its_expiry(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id)  # 5 days
    opened = auth_client.post(f"/api/inventory/items/{item['id']}/open", json={}).json()
    # shelf_life_days_opened=2 on the fixture product
    assert opened["expiry_date"] == (date.today() + timedelta(days=2)).isoformat()
    assert opened["opened_at"] == date.today().isoformat()


def test_stats_are_empty_before_any_data(auth_client: TestClient) -> None:
    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["active_items"] == 0
    assert stats["resolved_total"] == 0
    # No denominator yet — must be None rather than a misleading 0.0.
    assert stats["save_rate"] is None


def test_stats_compute_save_rate_and_money(auth_client: TestClient, seeded: dict) -> None:
    consumed = _create(auth_client, seeded["mozzarella"].id, quantity=2, unit_price_eur=1.29)
    wasted = _create(auth_client, seeded["ricotta"].id, quantity=1, unit_price_eur=2.50)
    _create(auth_client, seeded["pasta"].id, unit_price_eur=0.89)  # stays active

    auth_client.post(
        f"/api/inventory/items/{consumed['id']}/events", json={"event_type": "consumed"}
    )
    auth_client.post(f"/api/inventory/items/{wasted['id']}/events", json={"event_type": "wasted"})

    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["active_items"] == 1
    assert stats["consumed_total"] == 1
    assert stats["wasted_total"] == 1
    assert stats["eur_consumed"] == "2.58"  # 2 x 1.29
    assert stats["eur_wasted"] == "2.50"
    assert stats["save_rate"] == 0.5


def test_non_food_waste_is_excluded_from_the_ledger(auth_client: TestClient, seeded: dict) -> None:
    """Throwing out dish soap is not food waste and must not move the save rate."""
    detersivo = _create(auth_client, seeded["detersivo"].id, unit_price_eur=3.00)
    auth_client.post(
        f"/api/inventory/items/{detersivo['id']}/events", json={"event_type": "wasted"}
    )

    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["wasted_total"] == 1  # still counted as an item
    assert stats["eur_wasted"] == "0.00"  # but contributes no euros
    assert stats["save_rate"] is None  # and no food outcomes exist yet


def test_expiring_soon_counter(auth_client: TestClient, seeded: dict) -> None:
    _create(
        auth_client,
        seeded["mozzarella"].id,
        expiry_date=(date.today() + timedelta(days=1)).isoformat(),
    )
    _create(auth_client, seeded["pasta"].id)  # far future
    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["expiring_within_3d"] == 1


def test_expired_but_unresolved_items_are_flagged(auth_client: TestClient, seeded: dict) -> None:
    """These are the items the weekly nudge chases — an unresolved expired item is a
    missing label, which is worse for the model than a wasted one."""
    _create(
        auth_client,
        seeded["mozzarella"].id,
        expiry_date=(date.today() - timedelta(days=2)).isoformat(),
    )
    stats = auth_client.get("/api/inventory/stats").json()
    assert stats["expired_still_active"] == 1


def test_delete_removes_a_mistyped_item(auth_client: TestClient, seeded: dict) -> None:
    item = _create(auth_client, seeded["mozzarella"].id)
    assert auth_client.delete(f"/api/inventory/items/{item['id']}").status_code == 204
    assert auth_client.get("/api/inventory/items").json() == []


def test_events_on_a_missing_item_are_404(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/inventory/items/9999/events", json={"event_type": "consumed"})
    assert resp.status_code == 404
