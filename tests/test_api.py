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


def test_update_book():
    c = client()
    c.post("/books", json={"title": "A", "author": "B", "price": 1, "stock": 1})
    r = c.put("/books/1", json={"title": "A2", "author": "B2", "price": 5, "stock": 9})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "title": "A2", "author": "B2", "price": 5, "stock": 9}
    assert c.get("/books/1").json()["title"] == "A2"


def test_update_book_missing():
    r = client().put("/books/99", json={"title": "A", "author": "B", "price": 1})
    assert r.status_code == 404


def test_update_book_invalid():
    c = client()
    c.post("/books", json={"title": "A", "author": "B", "price": 1})
    assert c.put("/books/1", json={"title": "", "author": "B", "price": -1}).status_code == 422
