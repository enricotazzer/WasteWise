from fastapi.testclient import TestClient


def test_health_reports_database_ok(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_login_returns_a_token(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/token", data={"username": "test@example.com", "password": "testpassword"}
    )
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"


def test_wrong_password_is_rejected(client: TestClient) -> None:
    resp = client.post("/api/auth/token", data={"username": "test@example.com", "password": "nope"})
    assert resp.status_code == 401


def test_unknown_user_is_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/token", data={"username": "ghost@example.com", "password": "testpassword"}
    )
    assert resp.status_code == 401


def test_protected_routes_require_a_token(client: TestClient) -> None:
    assert client.get("/api/inventory/items").status_code == 401
    assert client.get("/api/catalog/products").status_code == 401
    assert client.get("/api/inventory/stats").status_code == 401


def test_garbage_token_is_rejected(client: TestClient) -> None:
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_me_returns_the_authenticated_user(auth_client: TestClient) -> None:
    body = auth_client.get("/api/auth/me").json()
    assert body["email"] == "test@example.com"
