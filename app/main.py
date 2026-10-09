import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0, default=0)
    genre: str | None = Field(default=None, min_length=1, max_length=50)


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


def create_app(rate_limit: int = 100, rate_window: float = 60.0) -> FastAPI:
    app = FastAPI(title="Bookstore API", version="1.0.0")
    started = time.monotonic()
    hits: dict[str, list[float]] = {}

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = [t for t in hits.get(ip, []) if now - t < rate_window]
        if len(recent) >= rate_limit:
            hits[ip] = recent
            retry = max(1, int(rate_window - (now - recent[0])) + 1)
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        recent.append(now)
        hits[ip] = recent
        return await call_next(request)

    books: dict[int, Book] = {}
    counter = {"next": 1}
    reviews: dict[int, list[Review]] = {}

    def with_stats(book: Book) -> Book:
        rs = reviews.get(book.id, [])
        avg = round(sum(r.rating for r in rs) / len(rs), 2) if rs else None
        return book.model_copy(update={"average_rating": avg, "review_count": len(rs)})

    @app.get("/health")
    def health() -> dict[str, str | float]:
        return {
            "status": "ok",
            "version": app.version,
            "uptime_seconds": round(time.monotonic() - started, 3),
        }

    @app.get("/books", response_model=list[Book])
    def list_books(
        author: str | None = Query(default=None),
        genre: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Book]:
        result = list(books.values())
        if author:
            result = [b for b in result if author.lower() in b.author.lower()]
        if genre:
            result = [b for b in result if b.genre and b.genre.lower() == genre.lower()]
        if q:
            result = [b for b in result if q.lower() in b.title.lower()]
        return [with_stats(b) for b in result[offset : offset + limit]]

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
        book = Book(id=counter["next"], **data.model_dump())
        counter["next"] += 1
        books[book.id] = book
        return with_stats(book)

    @app.get("/books/low-stock", response_model=list[Book])
    def low_stock(threshold: int = Query(default=3, ge=0)) -> list[Book]:
        return [with_stats(b) for b in books.values() if b.stock <= threshold]

    @app.get("/books/{book_id}", response_model=Book)
    def get_book(book_id: int) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return with_stats(books[book_id])

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
        books[book_id] = Book(id=book_id, **data.model_dump())
        return with_stats(books[book_id])

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
