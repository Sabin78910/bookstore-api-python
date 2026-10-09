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


def _seed(c: TestClient, n: int) -> None:
    for i in range(n):
        c.post("/books", json={"title": f"T{i}", "author": "A", "price": 1})


def test_list_books_default_limit():
    c = client()
    _seed(c, 25)
    assert len(c.get("/books").json()) == 20


def test_list_books_limit_offset():
    c = client()
    _seed(c, 5)
    ids = [b["id"] for b in c.get("/books", params={"limit": 2, "offset": 1}).json()]
    assert ids == [2, 3]


def test_list_books_offset_past_end():
    c = client()
    _seed(c, 3)
    assert c.get("/books", params={"offset": 10}).json() == []


def test_list_books_pagination_bounds():
    c = client()
    assert c.get("/books", params={"limit": 0}).status_code == 422
    assert c.get("/books", params={"limit": 101}).status_code == 422
    assert c.get("/books", params={"offset": -1}).status_code == 422
    assert c.get("/books", params={"limit": 100}).status_code == 200


def _add(c: TestClient, title: str, stock: int) -> None:
    r = c.post("/books", json={"title": title, "author": "A", "price": 1, "stock": stock})
    assert r.status_code == 201


def test_low_stock_default_threshold():
    c = client()
    for title, stock in [("a", 0), ("b", 3), ("c", 4)]:
        _add(c, title, stock)
    r = c.get("/books/low-stock")
    assert r.status_code == 200
    assert [b["title"] for b in r.json()] == ["a", "b"]


def test_low_stock_custom_threshold():
    c = client()
    for title, stock in [("a", 0), ("b", 3), ("c", 4)]:
        _add(c, title, stock)
    r = c.get("/books/low-stock", params={"threshold": 0})
    assert [b["title"] for b in r.json()] == ["a"]


def test_low_stock_negative_threshold_rejected():
    assert client().get("/books/low-stock", params={"threshold": -1}).status_code == 422
