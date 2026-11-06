from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Order(Base):
    """Database model representing a house pricing order."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_name: Mapped[str] = mapped_column(String(120), nullable=False)
    gr_liv_area: Mapped[float] = mapped_column(Float, nullable=False)
    total_bsmt_sf: Mapped[float] = mapped_column(Float, nullable=False)
    garage_area: Mapped[float] = mapped_column(Float, nullable=False)
    year_built: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_qual: Mapped[int] = mapped_column(Integer, nullable=False)
    ms_zoning: Mapped[str] = mapped_column(String(10), nullable=False)
    predicted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

class User(Base):
    """Database model representing a registered user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )