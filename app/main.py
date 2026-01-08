"""
FastAPI application entry point
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.services.load_balancer import ComfyUILoadBalancer
from app.services.tool_pool import ComfyUIToolPool
from app.routers import servers, templates, images, videos


# Ensure all directories exist
settings.ensure_directories()

# Initialize load balancer and tool pool
load_balancer = ComfyUILoadBalancer(settings.comfyui_servers)
tool_pool = ComfyUIToolPool(load_balancer)

# Initialize routers with dependencies
servers.init_router(load_balancer, tool_pool)
templates.init_router(tool_pool)
images.init_router(load_balancer, tool_pool)
videos.init_router(load_balancer, tool_pool)

# Create FastAPI application
app = FastAPI(
    title="Face Change API",
    description="API for image and video face swapping using ComfyUI",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(static_dir)
app.mount("/static", StaticFiles(directory=parent_dir), name="static")

# Include routers
app.include_router(servers.router)
app.include_router(templates.router)
app.include_router(images.router)
app.include_router(videos.router)


@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "Face Change API",
        "version": "2.0.0",
        "status": "running"
    }


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown"""
    load_balancer.shutdown()
