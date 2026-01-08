"""
Application startup script
"""
import uvicorn
from app.config import settings


if __name__ == '__main__':
    # Run with multiple workers for better concurrency
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers
    )
