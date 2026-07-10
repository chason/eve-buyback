import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class OperatorWalletToken(Base):
    """The instance OPERATOR's wallet credential (ADR-0042): one Fernet-encrypted EVE
    refresh token for the operator's own character, used by the payment-reconciliation
    job to read their wallet journal. Belongs to the app admin who connected it — never
    to a tenant corp (that's `corp_esi_tokens`). At most one row per instance; a
    re-authorization replaces it. Only ciphertext is stored; access tokens are never
    persisted."""

    __tablename__ = "operator_wallet_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # BigInteger: EVE character ids can exceed 2^31. No characters-FK: the operator's
    # character is not app data (it may never log in as a user).
    character_eve_id: Mapped[int] = mapped_column(BigInteger)
    character_name: Mapped[str]
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set when a refresh fails (revoked grant); surfaced on the admin wallet status.
    last_refresh_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
