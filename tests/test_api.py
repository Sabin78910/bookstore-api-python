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
        "isbn": None,
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


def _mk(c, title, author, genre=None):
    body = {"title": title, "author": author, "price": 5}
    if genre:
        body["genre"] = genre
    return c.post("/books", json=body).json()["id"]


def test_similar_books_ranked_by_rating_excluding_self():
    c = client()
    a = _mk(c, "A", "Ann", "Fiction")
    b = _mk(c, "B", "Ann")
    d = _mk(c, "D", "Bob", "fiction")
    _mk(c, "E", "Cy", "History")
    c.post(f"/books/{b}/reviews", json={"rating": 3})
    c.post(f"/books/{d}/reviews", json={"rating": 5})
    r = c.get(f"/books/{a}/similar")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [d, b]


def test_similar_books_unrated_last_and_404():
    c = client()
    a = _mk(c, "A", "Ann")
    b = _mk(c, "B", "Ann")
    d = _mk(c, "D", "Ann")
    c.post(f"/books/{d}/reviews", json={"rating": 1})
    assert [x["id"] for x in c.get(f"/books/{a}/similar").json()] == [d, b]
    assert c.get("/books/999/similar").status_code == 404


def _isbn_book(**kw):
    return {"title": "T", "author": "A", "price": 1, **kw}


def test_isbn_valid_10_and_13():
    c = client()
    r = c.post("/books", json=_isbn_book(isbn="0-306-40615-2"))
    assert r.status_code == 201
    assert r.json()["isbn"] == "0306406152"
    assert c.post("/books", json=_isbn_book(isbn="978-0-306-40615-7")).status_code == 201
    assert c.post("/books", json=_isbn_book(isbn="080442957X")).status_code == 201


def test_isbn_optional():
    assert client().post("/books", json=_isbn_book()).json()["isbn"] is None


def test_isbn_invalid_returns_422():
    c = client()
    for bad in ["0306406153", "9780306406158", "abc", "12345", ""]:
        r = c.post("/books", json=_isbn_book(isbn=bad))
        assert r.status_code == 422, bad
    assert "ISBN" in c.post("/books", json=_isbn_book(isbn="0306406153")).text


def test_duplicate_isbn_returns_409():
    c = client()
    assert c.post("/books", json=_isbn_book(isbn="0306406152")).status_code == 201
    r = c.post("/books", json=_isbn_book(isbn="9780306406157"))
    assert r.status_code == 201
    r = c.post("/books", json=_isbn_book(isbn="0-306-40615-2"))
    assert r.status_code == 409


def test_update_isbn_conflict_and_self():
    c = client()
    a = c.post("/books", json=_isbn_book(isbn="0306406152")).json()
    b = c.post("/books", json=_isbn_book(isbn="9780306406157")).json()
    assert c.put(f"/books/{a['id']}", json=_isbn_book(isbn="0306406152")).status_code == 200
    assert c.put(f"/books/{b['id']}", json=_isbn_book(isbn="0306406152")).status_code == 409


def test_writes_open_when_api_key_unset(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    c = TestClient(create_app())
    assert c.post("/books", json={"title": "T", "author": "A", "price": 1}).status_code == 201


def test_writes_require_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    c = TestClient(create_app())
    body = {"title": "T", "author": "A", "price": 1}
    assert c.post("/books", json=body).status_code == 401
    assert c.post("/books", json=body, headers={"X-API-Key": "bad"}).status_code == 401
    ok = c.post("/books", json=body, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 201
    assert c.put("/books/1", json=body).status_code == 401
    assert c.delete("/books/1").status_code == 401
    assert c.delete("/books/1", headers={"X-API-Key": "s3cret"}).status_code == 204


def test_reads_stay_public_with_api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "s3cret")
    c = TestClient(create_app())
    assert c.get("/books").status_code == 200
    assert c.get("/health").status_code == 200


def test_landing_page_html_with_live_data():
    c = client()
    c.post("/books", json={"title": "Dune <b>", "author": "Herbert", "price": 9})
    c.post("/books/1/reviews", json={"rating": 5})
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/docs" in r.text
    assert "Dune &lt;b&gt;" in r.text
    assert "Dune <b>" not in r.text


def test_landing_page_empty_store_and_json_unchanged():
    c = client()
    assert c.get("/").status_code == 200
    assert c.get("/books").json() == []
    assert c.get("/health").json()["status"] == "ok"


def test_cors_allowed_origin_on_health():
    origin = "https://sabin78910.github.io"
    r = client().get("/health", headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin


def test_cors_unknown_origin_gets_no_header():
    r = client().get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_preflight_denies_write_methods(monkeypatch):
    origin = "https://sabin78910.github.io"
    h = {"Origin": origin, "Access-Control-Request-Method": "POST"}
    assert client().options("/books", headers=h).status_code == 400
    h["Access-Control-Request-Method"] = "GET"
    assert client().options("/health", headers=h).status_code == 200


def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, https://b.example")
    r = client().get("/health", headers={"Origin": "https://b.example"})
    assert r.headers["access-control-allow-origin"] == "https://b.example"
    r = client().get("/health", headers={"Origin": "https://sabin78910.github.io"})
    assert "access-control-allow-origin" not in r.headers


def _etag_book(c: TestClient) -> int:
    return c.post("/books", json={"title": "T", "author": "A", "price": 5, "stock": 3}).json()["id"]


def test_etag_and_304_on_book_and_list():
    c = client()
    bid = _etag_book(c)
    for url in (f"/books/{bid}", "/books"):
        r = c.get(url)
        etag = r.headers["ETag"]
        assert etag.startswith('"') and r.status_code == 200
        r2 = c.get(url, headers={"If-None-Match": etag})
        assert r2.status_code == 304 and r2.content == b""
        assert r2.headers["ETag"] == etag
        assert c.get(url, headers={"If-None-Match": '"nope"'}).status_code == 200
        assert c.get(url, headers={"If-None-Match": f'"x", {etag}'}).status_code == 304


def test_etag_changes_after_update_sell_delete():
    c = client()
    bid = _etag_book(c)
    url = f"/books/{bid}"
    etag = c.get(url).headers["ETag"]
    c.put(url, json={"title": "T2", "author": "A", "price": 5, "stock": 3})
    r = c.get(url, headers={"If-None-Match": etag})
    assert r.status_code == 200 and r.headers["ETag"] != etag
    etag = r.headers["ETag"]
    c.post(f"{url}/sell")
    r = c.get(url, headers={"If-None-Match": etag})
    assert r.status_code == 200 and r.headers["ETag"] != etag
    list_etag = c.get("/books").headers["ETag"]
    c.delete(url)
    assert c.get(url, headers={"If-None-Match": etag}).status_code == 404
    r = c.get("/books", headers={"If-None-Match": list_etag})
    assert r.status_code == 200 and r.headers["ETag"] != list_etag


def _assert_problem(r, status, title):
    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "about:blank"
    assert body["status"] == status
    assert body["title"] == title
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def test_problem_json_404():
    body = _assert_problem(client().get("/books/99"), 404, "Not Found")
    assert body["detail"] == "Book not found"


def test_problem_json_422_keeps_field_errors():
    r = client().post("/books", json={"title": "", "author": "x", "price": -1})
    body = _assert_problem(r, 422, "Unprocessable Content")
    assert {e["loc"][-1] for e in body["errors"]} >= {"title", "price"}


def test_problem_json_429_keeps_retry_after():
    c = TestClient(create_app(rate_limit=1))
    c.get("/health")
    r = c.get("/health")
    body = _assert_problem(r, 429, "Too Many Requests")
    assert body["detail"] == "Rate limit exceeded"
    assert int(r.headers["Retry-After"]) >= 1


def test_problem_json_401(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    r = client().post("/books", json={"title": "a", "author": "b", "price": 1})
    body = _assert_problem(r, 401, "Unauthorized")
    assert body["detail"] == "Invalid or missing API key"


def _seed_sort():
    c = client()
    for t, a, p, g in [
        ("Banana", "Zed", 20, "x"),
        ("apple", "Amy", 10, "x"),
        ("Cherry", "Bob", 30, "y"),
        ("Date", "Amy", 10, "x"),
    ]:
        c.post("/books", json={"title": t, "author": a, "price": p, "genre": g})
    return c


def _titles(r):
    return [b["title"] for b in r.json()]


def test_sort_asc_desc():
    c = _seed_sort()
    assert _titles(c.get("/books", params={"sort": "price"})) == [
        "apple",
        "Date",
        "Banana",
        "Cherry",
    ]
    assert _titles(c.get("/books", params={"sort": "-price"})) == [
        "Cherry",
        "Banana",
        "apple",
        "Date",
    ]
    assert _titles(c.get("/books", params={"sort": "title"})) == [
        "apple",
        "Banana",
        "Cherry",
        "Date",
    ]
    assert _titles(c.get("/books", params={"sort": "-author"}))[0] == "Banana"


def test_sort_invalid_field_is_problem_422():
    r = _seed_sort().get("/books", params={"sort": "year"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert _seed_sort().get("/books", params={"sort": "-"}).status_code == 422


def test_sort_with_filters_and_pagination():
    c = _seed_sort()
    r = c.get("/books", params={"sort": "-price", "genre": "x", "limit": 2, "offset": 1})
    assert _titles(r) == ["apple", "Date"]
    r = c.get("/books", params={"sort": "price", "q": "a", "limit": 2})
    assert _titles(r) == ["apple", "Date"]


def test_no_sort_keeps_insertion_order():
    assert _titles(_seed_sort().get("/books")) == ["Banana", "apple", "Cherry", "Date"]


def _seed(c: TestClient, n: int, genre: str = "fic") -> None:
    for i in range(n):
        c.post(
            "/books",
            json={"title": f"B{i}", "author": "A", "price": 1, "genre": genre},
        )


def _links(r) -> dict[str, str]:
    out = {}
    for part in r.headers.get("link", "").split(", "):
        if part:
            url, rel = part.split("; rel=")
            out[rel.strip('"')] = url.strip("<>")
    return out


def test_pagination_first_page():
    c = client()
    _seed(c, 5)
    r = c.get("/books?limit=2&genre=fic")
    assert r.headers["x-total-count"] == "5"
    links = _links(r)
    assert set(links) == {"next"}
    assert "offset=2" in links["next"] and "limit=2" in links["next"]
    assert "genre=fic" in links["next"]


def test_pagination_middle_page():
    c = client()
    _seed(c, 5)
    r = c.get("/books?limit=2&offset=2")
    links = _links(r)
    assert set(links) == {"next", "prev"}
    assert "offset=4" in links["next"]
    assert "offset=0" in links["prev"]


def test_pagination_last_page():
    c = client()
    _seed(c, 5)
    r = c.get("/books?limit=2&offset=4")
    links = _links(r)
    assert set(links) == {"prev"}
    assert "offset=2" in links["prev"]


def test_pagination_prev_offset_clamped():
    c = client()
    _seed(c, 5)
    assert "offset=0" in _links(c.get("/books?limit=3&offset=1"))["prev"]


def test_pagination_single_page_has_no_link():
    c = client()
    _seed(c, 3)
    r = c.get("/books")
    assert r.headers["x-total-count"] == "3"
    assert "link" not in r.headers


def test_total_count_respects_filters():
    c = client()
    _seed(c, 3, "fic")
    _seed(c, 2, "sci")
    assert c.get("/books?genre=sci&limit=1").headers["x-total-count"] == "2"
    assert c.get("/books?q=B2").headers["x-total-count"] == "1"
    assert c.get("/books?author=zzz").headers["x-total-count"] == "0"


def test_pagination_headers_with_etag_304():
    c = client()
    _seed(c, 5)
    first = c.get("/books?limit=2")
    r = c.get("/books?limit=2", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert r.headers["x-total-count"] == "5"
    assert "next" in _links(r)


def test_cors_exposes_pagination_headers():
    origin = "https://sabin78910.github.io"
    r = client().get("/books", headers={"Origin": origin})
    exposed = r.headers["access-control-expose-headers"].lower()
    assert "x-total-count" in exposed and "link" in exposed


def _assert_baseline(r, csp=True):
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    if csp:
        assert r.headers["Content-Security-Policy"] == "frame-ancestors 'none'"


_BOOK = {"title": "T", "author": "A", "price": 1}


def test_security_headers_on_success_and_landing():
    c = client()
    _assert_baseline(c.get("/health"))
    _assert_baseline(c.get("/"))
    assert "Cache-Control" not in c.get("/health").headers


def test_security_headers_on_writes_no_store():
    c = client()
    r = c.post("/books", json=_BOOK)
    assert r.status_code == 201
    _assert_baseline(r)
    assert r.headers["Cache-Control"] == "no-store"
    assert c.put("/books/1", json=_BOOK).headers["Cache-Control"] == "no-store"
    r = c.delete("/books/1")
    assert r.status_code == 204
    assert r.headers["Cache-Control"] == "no-store"


def test_security_headers_on_errors_and_304():
    c = client()
    _assert_baseline(c.get("/books/99"))
    _assert_baseline(c.get("/books?limit=0"))
    r = c.get("/books")
    r304 = c.get("/books", headers={"If-None-Match": r.headers["ETag"]})
    assert r304.status_code == 304
    _assert_baseline(r304)


def test_security_headers_on_429_and_401(monkeypatch):
    c = TestClient(create_app(rate_limit=1, rate_window=60))
    c.get("/books")
    r = c.get("/books")
    assert r.status_code == 429
    _assert_baseline(r)
    monkeypatch.setenv("API_KEY", "k")
    c = client()
    r = c.post("/books", json=_BOOK)
    assert r.status_code == 401
    _assert_baseline(r)
    assert r.headers["Cache-Control"] == "no-store"


def test_docs_have_no_csp():
    r = client().get("/docs")
    assert r.status_code == 200
    assert "Content-Security-Policy" not in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
