from fastapi import APIRouter

from app.interface.v1 import (
    appraisals,
    auth,
    corporations,
    health,
    pricing,
    sde,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(corporations.router)
api_router.include_router(pricing.router)
api_router.include_router(appraisals.router)
api_router.include_router(sde.router)
