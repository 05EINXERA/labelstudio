"""FastAPI main entrypoint and facade (re-exports from `app.main`).

Maintains 100% backward compatibility for existing launch scripts (`uvicorn main:app`),
test runners, and system services.
"""
from app.main import (
    app,
    lifespan,
    health,
)

__all__ = ["app", "lifespan", "health"]

if __name__ == "__main__":
    import uvicorn
    from app.config import APP_HOST, APP_PORT
    import logging

    logger = logging.getLogger(__name__)
    logger.info("App running at http://%s:%s/", APP_HOST, APP_PORT)
    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=False)
