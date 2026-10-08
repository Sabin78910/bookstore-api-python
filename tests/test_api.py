from fastapi.testclient import TestClient

from app.main import create_app


def client() -> TestClient:
    return TestClient(create_app())


def test_health():
    assert client().get("/health").json() == {"status": "ok"}


def test_book_flow():
    c = client()
    r = c.post(
        "/books",
        json={"title": "Muna Madan", "author": "Laxmi Prasad Devkota", "price": 300, "stock": 2},
    )
    assert r.status_code == 201
    book_id = r.json()["id"]
    assert len(c.get("/books", params={"author": "devkota"}).json()) == 1
    assert c.post(f"/books/{book_id}/sell", params={"qty": 2}).json()["stock"] == 0
    assert c.post(f"/books/{book_id}/sell").status_code == 422
    assert c.delete(f"/books/{book_id}").status_code == 204
    assert c.get(f"/books/{book_id}").status_code == 404


def test_validation():
    assert (
        client().post("/books", json={"title": "", "author": "x", "price": -1}).status_code == 422
    )
