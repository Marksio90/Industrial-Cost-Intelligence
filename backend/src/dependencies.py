from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .middleware.auth import CurrentUser, get_current_user

# Re-export common dependencies
DBSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser  = Annotated[CurrentUser, Depends(get_current_user)]


class Pagination:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


PaginationDep = Annotated[Pagination, Depends(Pagination)]
