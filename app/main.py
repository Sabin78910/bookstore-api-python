from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0, default=0)


class Book(BookIn):
    id: int


def create_app() -> FastAPI:
    app = FastAPI(title="Bookstore API", version="1.0.0")
    books: dict[int, Book] = {}
    counter = {"next": 1}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/books", response_model=list[Book])
    def list_books(
        author: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Book]:
        result = list(books.values())
        if author:
            result = [b for b in result if author.lower() in b.author.lower()]
        return result[offset : offset + limit]

    @app.post("/books", response_model=Book, status_code=201)
    def create_book(data: BookIn) -> Book:
        book = Book(id=counter["next"], **data.model_dump())
        counter["next"] += 1
        books[book.id] = book
        return book

    @app.get("/books/{book_id}", response_model=Book)
    def get_book(book_id: int) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return books[book_id]

    @app.post("/books/{book_id}/sell", response_model=Book)
    def sell(book_id: int, qty: int = Query(default=1, ge=1)) -> Book:
        book = get_book(book_id)
        if book.stock < qty:
            raise HTTPException(422, "Insufficient stock")
        book.stock -= qty
        return book

    @app.delete("/books/{book_id}", status_code=204)
    def delete_book(book_id: int) -> None:
        if books.pop(book_id, None) is None:
            raise HTTPException(404, "Book not found")

    return app


app = create_app()
