from fastapi.testclient import TestClient

from app.main import create_app


def client() -> TestClient:
    return TestClient(create_app())


def test_health():
    body = client().get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["uptime_seconds"] >= 0


def test_rate_limit_returns_429():
    c = TestClient(create_app(rate_limit=3, rate_window=60))
    assert [c.get("/books").status_code for _ in range(3)] == [200] * 3
    r = c.get("/books")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_window_resets(monkeypatch):
    import app.main as m

    now = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    c = TestClient(create_app(rate_limit=1, rate_window=10))
    assert c.get("/books").status_code == 200
    assert c.get("/books").status_code == 429
    now[0] += 11
    assert c.get("/books").status_code == 200


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
    assert r.json() == {
        "id": 1,
        "title": "A2",
        "author": "B2",
        "price": 5,
        "stock": 9,
        "genre": None,
        "average_rating": None,
        "review_count": 0,
    }
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


def test_search_by_title():
    c = client()
    for title, author in [
        ("Muna Madan", "Devkota"),
        ("Madan Puraskar", "Other"),
        ("Basain", "Koirala"),
    ]:
        c.post("/books", json={"title": title, "author": author, "price": 1})
    assert len(c.get("/books", params={"q": "MADAN"}).json()) == 2
    assert len(c.get("/books", params={"q": "madan", "author": "devkota"}).json()) == 1
    assert c.get("/books", params={"q": "zzz"}).json() == []


def test_low_stock_report():
    c = client()
    for title, stock in [("A", 0), ("B", 3), ("C", 4), ("D", 10)]:
        c.post("/books", json={"title": title, "author": "X", "price": 1, "stock": stock})
    r = c.get("/books/low-stock")
    assert r.status_code == 200
    assert [b["title"] for b in r.json()] == ["A", "B"]
    r = c.get("/books/low-stock", params={"threshold": 4})
    assert [b["title"] for b in r.json()] == ["A", "B", "C"]
    assert c.get("/books/low-stock", params={"threshold": -1}).status_code == 422


def _book(c: TestClient) -> int:
    r = c.post("/books", json={"title": "T", "author": "A", "price": 1})
    return r.json()["id"]


def test_book_without_reviews_has_no_rating():
    c = client()
    body = c.get(f"/books/{_book(c)}").json()
    assert body["average_rating"] is None
    assert body["review_count"] == 0


def test_create_and_list_reviews():
    c = client()
    bid = _book(c)
    r = c.post(f"/books/{bid}/reviews", json={"rating": 5, "text": "Great"})
    assert r.status_code == 201
    assert r.json()["rating"] == 5
    c.post(f"/books/{bid}/reviews", json={"rating": 2, "text": "Meh"})
    reviews = c.get(f"/books/{bid}/reviews").json()
    assert [x["text"] for x in reviews] == ["Great", "Meh"]


def test_book_includes_average_and_count():
    c = client()
    bid = _book(c)
    c.post(f"/books/{bid}/reviews", json={"rating": 5, "text": ""})
    c.post(f"/books/{bid}/reviews", json={"rating": 2, "text": ""})
    for body in (c.get(f"/books/{bid}").json(), c.get("/books").json()[0]):
        assert body["average_rating"] == 3.5
        assert body["review_count"] == 2


def test_review_validation():
    c = client()
    bid = _book(c)
    for bad in (
        {"rating": 0, "text": ""},
        {"rating": 6, "text": ""},
        {"rating": 3, "text": "x" * 1001},
    ):
        assert c.post(f"/books/{bid}/reviews", json=bad).status_code == 422
    ok = {"rating": 3, "text": "x" * 1000}
    assert c.post(f"/books/{bid}/reviews", json=ok).status_code == 201


def test_reviews_unknown_book_404():
    c = client()
    assert c.get("/books/99/reviews").status_code == 404
    r = c.post("/books/99/reviews", json={"rating": 3, "text": ""})
    assert r.status_code == 404


def test_update_and_delete_keep_review_consistency():
    c = client()
    bid = _book(c)
    c.post(f"/books/{bid}/reviews", json={"rating": 4, "text": ""})
    r = c.put(f"/books/{bid}", json={"title": "N", "author": "A", "price": 2})
    assert r.json()["review_count"] == 1
    c.delete(f"/books/{bid}")
    assert c.get(f"/books/{bid}/reviews").status_code == 404


def _add(c, title, author, genre=None):
    body = {"title": title, "author": author, "price": 1}
    if genre:
        body["genre"] = genre
    return c.post("/books", json=body).json()["id"]


def test_filter_by_genre_and_author():
    c = client()
    _add(c, "A", "Ann", "Fiction")
    _add(c, "B", "Bob", "fiction")
    _add(c, "C", "Ann", "History")
    _add(c, "D", "Ann")
    assert len(c.get("/books", params={"genre": "FICTION"}).json()) == 2
    both = c.get("/books", params={"genre": "fiction", "author": "ann"}).json()
    assert [b["title"] for b in both] == ["A"]
    assert c.get("/books", params={"genre": "nope"}).json() == []


def test_genres_with_counts():
    c = client()
    assert c.get("/genres").json() == []
    _add(c, "A", "Ann", "Fiction")
    _add(c, "B", "Bob", "fiction")
    _add(c, "C", "Ann", "History")
    _add(c, "D", "Ann")
    assert c.get("/genres").json() == [
        {"genre": "fiction", "count": 2},
        {"genre": "history", "count": 1},
    ]
