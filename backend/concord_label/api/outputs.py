"""Outputs other apps read through the model catalog's output index (platform/catalog/README.md).

The catalog indexes Label's `model_output` rows by build key, audio, span and params; an app that finds one
of them in the index reads it here by id, as the contract says every app serves:
`POST /api/outputs/batch` with `{"ids": [...]}` -> `{"outputs": [...]}`, missing ids left out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db import get_session
from ..runs import cache

router = APIRouter(tags=["outputs"])


class OutputsIn(BaseModel):
    ids: list[int] = Field(max_length=5000)


@router.post("/outputs/batch")
def outputs_batch(body: OutputsIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = cache.outputs_for(session, body.ids)
    return {"outputs": [cache.served(rows[i]) for i in dict.fromkeys(body.ids) if i in rows]}
