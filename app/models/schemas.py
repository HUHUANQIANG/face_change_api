"""
Pydantic models for API request and response schemas
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ServerStatusResponse(BaseModel):
    """Response model for server status"""
    servers: Dict[str, Dict[str, Any]] = Field(..., description="Dictionary of server addresses and their status")
    total_servers: int = Field(..., description="Total number of servers")


class ServerActionResponse(BaseModel):
    """Response model for server add/remove actions"""
    status: str = Field(..., description="Action status")
    message: str = Field(..., description="Action message")


class TemplatesResponse(BaseModel):
    """Response model for templates list"""
    templates: List[str] = Field(..., description="List of available template files")
    mode: str = Field(default="image", description="Template mode (image or video)")
    message: Optional[str] = Field(None, description="Optional message")


class LoadTemplateResponse(BaseModel):
    """Response model for load template action"""
    status: str = Field(..., description="Action status")
    message: str = Field(..., description="Action message")
    info: str = Field(..., description="Detailed information about the action")
    mode: str = Field(..., description="Template mode (image or video)")


class ProcessImageResponse(BaseModel):
    """Response model for image processing"""
    status: str = Field(..., description="Processing status")
    original_image: str = Field(..., description="Path to original image")
    processed_image_base64: Optional[str] = Field(None, description="Base64 encoded processed image")
    processed_image_path: Optional[str] = Field(None, description="Path to processed image")
    processed_image_url: Optional[str] = Field(None, description="URL to processed image")
    server_used: str = Field(..., description="Server address used for processing")
    message: str = Field(..., description="Processing message")


class ProcessVideoResponse(BaseModel):
    """Response model for video processing"""
    status: str = Field(..., description="Processing status")
    original_video: str = Field(..., description="Path to original video")
    processed_video_base64: Optional[str] = Field(None, description="Base64 encoded processed video")
    processed_video_path: Optional[str] = Field(None, description="Path to processed video")
    processed_video_url: Optional[str] = Field(None, description="URL to processed video")
    server_used: str = Field(..., description="Server address used for processing")
    message: str = Field(..., description="Processing message")
