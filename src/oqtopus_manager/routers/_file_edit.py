"""Request bodies for file lock/edit/save routes."""

from __future__ import annotations

from pydantic import BaseModel


class _UnlockBody(BaseModel):
    token: str


class _SaveBody(BaseModel):
    token: str
    content: str
