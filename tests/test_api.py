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


def _seed(c: TestClient, n: int) -> None:
    for i in range(n):
        c.post("/books", json={"title": f"T{i}", "author": "A", "price": 1})


def test_pagination_defaults_to_20():
    c = client()
    _seed(c, 25)
    assert len(c.get("/books").json()) == 20


def test_pagination_limit_and_offset():
    c = client()
    _seed(c, 5)
    r = c.get("/books", params={"limit": 2, "offset": 3})
    assert [b["id"] for b in r.json()] == [4, 5]


def test_pagination_offset_past_end_is_empty():
    c = client()
    _seed(c, 3)
    assert c.get("/books", params={"offset": 10}).json() == []


def test_pagination_bounds():
    c = client()
    assert c.get("/books", params={"limit": 0}).status_code == 422
    assert c.get("/books", params={"limit": 101}).status_code == 422
    assert c.get("/books", params={"offset": -1}).status_code == 422
    assert c.get("/books", params={"limit": 100}).status_code == 200
    assert c.get("/books", params={"limit": 1}).status_code == 200
