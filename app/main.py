import hashlib
import html
import json
import math
import os
import re
import secrets
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware


def normalize_isbn(value: str) -> str:
    isbn = value.replace("-", "").replace(" ", "").upper()
    if len(isbn) == 10 and isbn[:9].isdigit() and (isbn[9].isdigit() or isbn[9] == "X"):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn))
        ok = total % 11 == 0
    elif len(isbn) == 13 and isbn.isdigit():
        total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(isbn))
        ok = total % 10 == 0
    else:
        ok = False
    if not ok:
        raise ValueError("Invalid ISBN: must be a valid ISBN-10 or ISBN-13")
    return isbn


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0, default=0)
    genre: str | None = Field(default=None, min_length=1, max_length=50)
    isbn: str | None = None

    @field_validator("isbn")
    @classmethod
    def check_isbn(cls, v: str | None) -> str | None:
        return None if v is None else normalize_isbn(v)


class BookPatch(BaseModel):
    """Merge-patch body: only supplied fields change; null clears optional fields."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    author: str | None = Field(default=None, min_length=1, max_length=100)
    price: float | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    genre: str | None = Field(default=None, min_length=1, max_length=50)
    isbn: str | None = None

    @field_validator("isbn")
    @classmethod
    def check_isbn(cls, v: str | None) -> str | None:
        return None if v is None else normalize_isbn(v)

    @model_validator(mode="after")
    def required_not_null(self) -> "BookPatch":
        for name in ("title", "author", "price", "stock"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class Book(BookIn):
    id: int
    average_rating: float | None = None
    review_count: int = 0


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=1000)


class Review(ReviewIn):
    id: int
    book_id: int


REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def problem(
    status: int, detail: str, headers: dict[str, str] | None = None, **extra: object
) -> JSONResponse:
    """RFC 9457 problem+json response."""
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase if status != 422 else "Unprocessable Content",
        "status": status,
        "detail": detail,
        **extra,
    }
    if (rid := request_id_var.get()) is not None:
        body["request_id"] = rid
    return JSONResponse(
        jsonable_encoder(body),
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


IDEMPOTENCY_MAX_ENTRIES = 1000
IDEMPOTENCY_KEY_MAX_LEN = 255
IDEMPOTENT_PATHS = re.compile(r"^/books(/\d+/sell)?$")

MAX_BODY_BYTES = 64 * 1024
BODY_METHODS = {"POST", "PUT", "PATCH"}


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Reject bodies over MAX_BODY_BYTES with 413, by Content-Length or while streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in BODY_METHODS:
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return await self._reject(scope, receive, send)
        seen = 0
        exceeded = False

        async def limited():
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY_BYTES:
                    exceeded = True
                    raise BodyTooLarge
            return message

        async def guarded_send(message):
            # FastAPI turns the read error into a 400; swap it for the 413
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, limited, guarded_send)
        except BodyTooLarge:
            pass
        if exceeded:
            await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send):
        response = problem(413, f"Request body exceeds {MAX_BODY_BYTES} bytes")
        await response(scope, receive, send)


SORT_FIELDS = ("title", "author", "price")


LANDING_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bookstore API</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,sans-serif;color:#e8e8f0;min-height:100vh;
background:radial-gradient(circle at 20% 0,#2a2350,#0e0f1a 60%) fixed}
a{color:#ffd166}
.hero{padding:56px 24px 24px;text-align:center}
.hero h1{margin:0 0 8px;font-size:2.5rem}
main{max-width:960px;margin:0 auto;padding:24px}
.glass{background:#ffffff12;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
border:1px solid #ffffff25;border-radius:12px;padding:16px}
input,select,textarea,button{font:inherit;color:inherit;background:#00000040;
border:1px solid #ffffff30;border-radius:8px;padding:10px}
button{cursor:pointer;background:#ffd166;color:#14141f;font-weight:700;border:0}
#search{width:100%;font-size:1.1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:14px;margin:16px 0}
.book{cursor:pointer;padding:0;overflow:hidden;color:inherit;text-align:left}
.cover{height:130px;display:flex;align-items:flex-end;padding:10px;font-weight:700;
line-height:1.2;color:#fff;text-shadow:0 1px 3px #0008}
.meta{padding:10px;font-size:.85rem;color:#c9c9d8}
.stars{color:#ffd166}
#status,li span{color:#a0a0b8}
pre{background:#00000050;padding:12px;border-radius:8px;overflow:auto;max-height:320px}
.row{display:flex;gap:8px;flex-wrap:wrap}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}
</style></head><body>
<section class="hero"><h1>Bookstore API</h1>
<p>Books, genres and reviews over REST.</p>
<a href="/docs">Explore the API docs &rarr;</a></section>
<main>
<div class="glass"><input id="search" type="search" placeholder="Search books by title…"
aria-label="Search books by title"><p id="status" aria-live="polite"></p></div>
<div id="results" class="grid"></div>
<h2 id="similar-title" hidden>Similar titles</h2>
<div id="similar" class="grid"></div>
<h2>API playground</h2>
<div id="playground" class="glass">
<div class="row"><select id="pg-method" aria-label="Method">
<option>GET</option></select>
<input id="pg-path" value="/books?limit=3" size="40" aria-label="Path">
<button id="pg-send" type="button">Send</button></div>
<pre id="pg-out">Response appears here.</pre></div>
<p class="count"><b>{{COUNT}}</b> books in the catalog</p>
<h2>Top rated</h2><ul>{{TOP}}</ul>
<h2>Endpoints</h2><div class="cards">{{ENDPOINTS}}</div>
</main>
<script>
const $ = (id) => document.getElementById(id);
function hue(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function card(b) {
  const el = document.createElement("button");
  el.type = "button"; el.className = "glass book";
  const cover = document.createElement("div"); cover.className = "cover";
  const h = hue(b.title + b.author);
  const h2 = (h + 60) % 360;
  cover.style.background =
    `linear-gradient(135deg,hsl(${h},60%,45%),hsl(${h2},65%,30%))`;
  cover.textContent = b.title;
  const meta = document.createElement("div"); meta.className = "meta";
  const by = document.createElement("div"); by.textContent = b.author;
  const r = document.createElement("div"); r.className = "stars";
  r.textContent = b.average_rating == null ? "No ratings"
    : "\\u2605".repeat(Math.round(b.average_rating)) + " " + b.average_rating;
  meta.append(by, r); el.append(cover, meta);
  el.addEventListener("click", () => showSimilar(b));
  return el;
}
function fill(box, books) { box.replaceChildren(...books.map(card)); }
async function api(path) {
  const res = await fetch(path);
  return { res, data: await res.json() };
}
async function search() {
  const q = $("search").value.trim();
  const { res, data } = await api("/books?limit=24" + (q ? "&q=" + encodeURIComponent(q) : ""));
  $("status").textContent = res.ok
    ? (res.headers.get("X-Total-Count") || 0) + " matching books" : "Search failed";
  fill($("results"), res.ok ? data : []);
}
async function showSimilar(b) {
  const { res, data } = await api("/books/" + b.id + "/similar");
  $("similar-title").hidden = false;
  $("similar-title").textContent = "Similar to " + b.title;
  fill($("similar"), res.ok ? data.slice(0, 6) : []);
}
let timer;
$("search").addEventListener("input", () => {
  clearTimeout(timer); timer = setTimeout(search, 250);
});
$("pg-send").addEventListener("click", async () => {
  const path = $("pg-path").value.trim();
  if (!path.startsWith("/")) { $("pg-out").textContent = "Path must start with /"; return; }
  try {
    const res = await fetch(path, { method: $("pg-method").value });
    const text = await res.text();
    let body = text;
    try { body = JSON.stringify(JSON.parse(text), null, 2); } catch (e) {}
    $("pg-out").textContent = res.status + " " + res.statusText + "\\n\\n" + body;
  } catch (e) { $("pg-out").textContent = "Request failed"; }
});
search();
</script></body></html>"""


def create_app(rate_limit: int = 100, rate_window: float = 60.0) -> FastAPI:
    app = FastAPI(title="Bookstore API", version="1.0.0")
    started = time.monotonic()
    hits: dict[str, list[float]] = {}

    def rate_headers(remaining: int, reset: int) -> dict[str, str]:
        return {
            "RateLimit-Limit": str(rate_limit),
            "RateLimit-Remaining": str(max(0, remaining)),
            "RateLimit-Reset": str(reset),
        }

    # key -> (fingerprint, status, body, content-type); oldest evicted first
    idempotency: OrderedDict[str, tuple[str, int, bytes, str | None]] = OrderedDict()

    @app.middleware("http")
    async def idempotency_keys(request: Request, call_next):
        key = request.headers.get("Idempotency-Key")
        if key is None or request.method != "POST" or not IDEMPOTENT_PATHS.match(request.url.path):
            return await call_next(request)
        if not (1 <= len(key) <= IDEMPOTENCY_KEY_MAX_LEN and all(" " <= c <= "~" for c in key)):
            return problem(
                422,
                f"Idempotency-Key must be 1-{IDEMPOTENCY_KEY_MAX_LEN} printable ASCII characters",
            )
        raw = request.url.path + "?" + request.url.query + "\n"
        fingerprint = hashlib.sha256(raw.encode() + await request.body()).hexdigest()
        saved = idempotency.get(key)
        if saved is not None:
            if saved[0] != fingerprint:
                return problem(422, "Idempotency-Key was already used with a different request")
            return Response(
                saved[2],
                status_code=saved[1],
                media_type=saved[3],
                headers={"Idempotent-Replayed": "true"},
            )
        response = await call_next(request)
        if response.status_code >= 500:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        idempotency[key] = (
            fingerprint,
            response.status_code,
            body,
            response.headers.get("content-type"),
        )
        while len(idempotency) > IDEMPOTENCY_MAX_ENTRIES:
            idempotency.popitem(last=False)
        return Response(
            body,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k != "content-length"},
        )

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = [t for t in hits.get(ip, []) if now - t < rate_window]
        if len(recent) >= rate_limit:
            hits[ip] = recent
            reset = max(0, math.ceil(rate_window - (now - recent[0])))
            return problem(
                429,
                "Rate limit exceeded",
                {**rate_headers(0, reset), "Retry-After": str(max(1, reset))},
            )
        recent.append(now)
        hits[ip] = recent
        reset = max(0, math.ceil(rate_window - (now - recent[0])))
        response = await call_next(request)
        response.headers.update(rate_headers(rate_limit - len(recent), reset))
        return response

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        key = os.environ.get("API_KEY")
        if key and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            given = request.headers.get("X-API-Key", "")
            if not secrets.compare_digest(given.encode(), key.encode()):
                return problem(401, "Invalid or missing API key")
        return await call_next(request)

    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            response.headers["Cache-Control"] = "no-store"
        if request.url.path not in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return problem(422, "Request validation failed", errors=exc.errors())

    origins = [
        o.strip()
        for o in os.environ.get("ALLOWED_ORIGINS", "https://sabin78910.github.io").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        expose_headers=[
            "X-Total-Count",
            "Link",
            "RateLimit-Limit",
            "RateLimit-Remaining",
            "RateLimit-Reset",
            "X-Request-ID",
        ],
    )

    async def request_id(request: Request, call_next):
        given = request.headers.get("X-Request-ID", "")
        rid = given if REQUEST_ID_RE.fullmatch(given) else str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    # added last so it is outermost and covers every response
    app.add_middleware(BaseHTTPMiddleware, dispatch=request_id)

    books: dict[int, Book] = {}
    counter = {"next": 1}
    reviews: dict[int, list[Review]] = {}

    def with_stats(book: Book) -> Book:
        rs = reviews.get(book.id, [])
        avg = round(sum(r.rating for r in rs) / len(rs), 2) if rs else None
        return book.model_copy(update={"average_rating": avg, "review_count": len(rs)})

    def conditional(
        request: Request, payload: object, extra: dict[str, str] | None = None
    ) -> Response:
        body = json.dumps(
            jsonable_encoder(payload), separators=(",", ":"), ensure_ascii=False
        ).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        sent = [
            t.strip().removeprefix("W/")
            for t in request.headers.get("if-none-match", "").split(",")
        ]
        if etag in sent or "*" in sent:
            return Response(status_code=304, headers={**(extra or {}), "ETag": etag})
        return Response(
            body, media_type="application/json", headers={**(extra or {}), "ETag": etag}
        )

    def page_url(request: Request, limit: int, offset: int) -> str:
        return str(request.url.include_query_params(limit=limit, offset=offset))

    def check_isbn_free(isbn: str | None, exclude: int | None = None) -> None:
        if isbn and any(b.isbn == isbn and b.id != exclude for b in books.values()):
            raise HTTPException(409, "A book with this ISBN already exists")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing() -> str:
        rated = sorted(
            (with_stats(b) for b in books.values()),
            key=lambda b: (-(b.average_rating or 0), b.id),
        )
        top = [b for b in rated if b.average_rating is not None][:5]
        items = (
            "".join(
                f"<li>{html.escape(b.title)} <span>by {html.escape(b.author)}"
                f" &middot; {b.average_rating}&#9733;</span></li>"
                for b in top
            )
            or "<li>No rated books yet.</li>"
        )
        cards = "".join(
            f"<div class='card'><b>{m}</b> <code>{p}</code><p>{d}</p></div>"
            for m, p, d in [
                ("GET", "/books", "Browse and search books"),
                ("POST", "/books", "Add a book"),
                ("GET", "/genres", "Genres with counts"),
                ("GET", "/health", "Service status"),
            ]
        )
        return (
            LANDING_HTML.replace("{{COUNT}}", str(len(books)))
            .replace("{{TOP}}", items)
            .replace("{{ENDPOINTS}}", cards)
        )

    @app.get("/health")
    def health() -> dict[str, str | float]:
        return {
            "status": "ok",
            "version": app.version,
            "uptime_seconds": round(time.monotonic() - started, 3),
        }

    @app.get("/books", response_model=list[Book])
    def list_books(
        request: Request,
        author: str | None = Query(default=None),
        genre: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        sort: str | None = Query(default=None, description="title, author or price; '-' = desc"),
    ) -> Response:
        field = (sort or "").removeprefix("-")
        if sort is not None and field not in SORT_FIELDS:
            raise HTTPException(422, f"Invalid sort field; allowed: {', '.join(SORT_FIELDS)}")
        result = list(books.values())
        if author:
            result = [b for b in result if author.lower() in b.author.lower()]
        if genre:
            result = [b for b in result if b.genre and b.genre.lower() == genre.lower()]
        if q:
            result = [b for b in result if q.lower() in b.title.lower()]
        if sort:
            result.sort(
                key=lambda b: v.lower() if isinstance(v := getattr(b, field), str) else v,
                reverse=sort.startswith("-"),
            )
        total = len(result)
        headers = {"X-Total-Count": str(total)}
        links = []
        if offset + limit < total:
            links.append(f'<{page_url(request, limit, offset + limit)}>; rel="next"')
        if offset > 0:
            links.append(f'<{page_url(request, limit, max(0, offset - limit))}>; rel="prev"')
        if links:
            headers["Link"] = ", ".join(links)
        page = [with_stats(b) for b in result[offset : offset + limit]]
        return conditional(request, page, headers)

    @app.get("/genres")
    def list_genres() -> list[dict[str, str | int]]:
        counts: dict[str, int] = {}
        for b in books.values():
            if b.genre:
                key = b.genre.lower()
                counts[key] = counts.get(key, 0) + 1
        return [{"genre": g, "count": c} for g, c in sorted(counts.items())]

    @app.post("/books", response_model=Book, status_code=201)
    def create_book(data: BookIn) -> Book:
        check_isbn_free(data.isbn)
        book = Book(id=counter["next"], **data.model_dump())
        counter["next"] += 1
        books[book.id] = book
        return with_stats(book)

    @app.get("/books/low-stock", response_model=list[Book])
    def low_stock(threshold: int = Query(default=3, ge=0)) -> list[Book]:
        return [with_stats(b) for b in books.values() if b.stock <= threshold]

    @app.get("/books/{book_id}", response_model=Book)
    def get_book(book_id: int, request: Request) -> Response:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return conditional(request, with_stats(books[book_id]))

    @app.get("/books/{book_id}/similar", response_model=list[Book])
    def similar_books(book_id: int) -> list[Book]:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        src = books[book_id]
        genre = src.genre.lower() if src.genre else None
        found = [
            with_stats(b)
            for b in books.values()
            if b.id != book_id
            and (
                b.author.lower() == src.author.lower()
                or (genre and b.genre and b.genre.lower() == genre)
            )
        ]
        found.sort(key=lambda b: (-(b.average_rating or 0), b.id))
        return found

    @app.put("/books/{book_id}", response_model=Book)
    def update_book(book_id: int, data: BookIn) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        check_isbn_free(data.isbn, exclude=book_id)
        books[book_id] = Book(id=book_id, **data.model_dump())
        return with_stats(books[book_id])

    @app.patch("/books/{book_id}", response_model=Book)
    def patch_book(book_id: int, data: BookPatch) -> Response:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        changes = data.model_dump(exclude_unset=True)
        if changes.get("isbn"):
            check_isbn_free(changes["isbn"], exclude=book_id)
        books[book_id] = books[book_id].model_copy(update=changes)
        body = json.dumps(
            jsonable_encoder(with_stats(books[book_id])), separators=(",", ":"), ensure_ascii=False
        ).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        return Response(body, media_type="application/json", headers={"ETag": etag})

    @app.post("/books/{book_id}/sell", response_model=Book)
    def sell(book_id: int, qty: int = Query(default=1, ge=1)) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        book = books[book_id]
        if book.stock < qty:
            raise HTTPException(422, "Insufficient stock")
        book.stock -= qty
        return with_stats(book)

    @app.delete("/books/{book_id}", status_code=204)
    def delete_book(book_id: int) -> None:
        if books.pop(book_id, None) is None:
            raise HTTPException(404, "Book not found")
        reviews.pop(book_id, None)

    @app.post("/books/{book_id}/reviews", response_model=Review, status_code=201)
    def add_review(book_id: int, data: ReviewIn) -> Review:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        items = reviews.setdefault(book_id, [])
        review = Review(id=len(items) + 1, book_id=book_id, **data.model_dump())
        items.append(review)
        return review

    @app.get("/books/{book_id}/reviews", response_model=list[Review])
    def list_reviews(book_id: int) -> list[Review]:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return reviews.get(book_id, [])

    return app


app = create_app()
