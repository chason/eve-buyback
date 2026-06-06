from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.db import get_session

# A DB session injected at the interface boundary and passed into use cases.
SessionDep = Annotated[AsyncSession, Depends(get_session)]
