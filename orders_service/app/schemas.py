from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class OrderBase(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=120)
    gr_liv_area: float = Field(..., gt=0, le=10000)
    total_bsmt_sf: float = Field(..., ge=0, le=10000)
    garage_area: float = Field(..., ge=0, le=2000)
    year_built: int = Field(..., ge=1872, le=2026)
    overall_qual: int = Field(..., ge=1, le=10)
    ms_zoning: str = Field(..., min_length=1, max_length=10)


class OrderCreate(OrderBase):
    pass


class OrderUpdate(BaseModel):
    client_name: str | None = Field(None, min_length=2, max_length=120)
    gr_liv_area: float | None = Field(None, gt=0, le=10000)
    total_bsmt_sf: float | None = Field(None, ge=0, le=10000)
    garage_area: float | None = Field(None, ge=0, le=2000)
    year_built: int | None = Field(None, ge=1872, le=2026)
    overall_qual: int | None = Field(None, ge=1, le=10)
    ms_zoning: str | None = Field(None, min_length=1, max_length=10)


class OrderRead(OrderBase):
    id: int
    predicted_price: float | None = None
    created_at: datetime
    status: str = "pending"

    model_config = ConfigDict(from_attributes=True)

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=72)  # обмеження bcrypt

class UserOut(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    username: str | None = None

class BatchOrderResult(BaseModel):
    index: int
    order_id: int
    status: str
    predicted_price: Optional[float] = None
    error: Optional[str] = None

class BatchOrderResponse(BaseModel):
    total: int
    results: List[BatchOrderResult]
