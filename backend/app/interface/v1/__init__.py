from fastapi import APIRouter

from app.interface.v1 import auth, corporations, health, sde

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(corporations.router)
api_router.include_router(sde.router)
