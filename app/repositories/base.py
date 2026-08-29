"""Small repository primitives shared by Member B persistence adapters."""

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)
KeyT = TypeVar("KeyT")


class Repository(Generic[ModelT]):
    def __init__(self, db: Session, model: type[ModelT]) -> None:
        self.db = db
        self.model = model

    def get(self, key: KeyT) -> ModelT | None:
        return self.db.get(self.model, key)

    def list(self) -> list[ModelT]:
        return list(self.db.scalars(select(self.model)).all())

    def add(self, entity: ModelT) -> ModelT:
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def delete(self, entity: ModelT) -> None:
        self.db.delete(entity)
        self.db.commit()

