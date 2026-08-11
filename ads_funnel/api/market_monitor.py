"""Compatibility facade for the market-monitor FastAPI router."""

from .market_monitoring.router import router

__all__ = ["router"]
