"""Annotated models — the data map lives here, on the models themselves."""

from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from effaced import (
    ErasureStrategy,
    LegalBasis,
    PiiCategory,
    RetentionPolicy,
    pii,
    subject_link,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    """The data subject. subject_link("") marks it as such."""

    __tablename__ = "users"
    __table_args__ = {"info": subject_link("")}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        info=pii(PiiCategory.CONTACT, legal_basis=LegalBasis.CONTRACT, purpose="account login")
    )
    display_name: Mapped[str] = mapped_column(info=pii(PiiCategory.IDENTITY))
    theme: Mapped[str] = mapped_column(default="dark")  # not personal data


class Invoice(Base):
    """Reaches the subject through its `user` relationship; the billing
    address is legally retained, so erasure anonymizes instead of deleting."""

    __tablename__ = "invoices"
    __table_args__ = {"info": subject_link("user")}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped[User] = relationship()
    billing_address: Mapped[str] = mapped_column(
        info=pii(
            PiiCategory.FINANCIAL,
            erasure=ErasureStrategy.RETAIN,
            retention=RetentionPolicy(reason="§147 AO — 10-year invoice retention"),
        )
    )
