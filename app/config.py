"""
Configuration management using pydantic-settings
"""
import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support"""
    
    # Directory configurations
    upload_dir: str = Field(default="uploaded_images", description="Directory for uploaded images")
    processed_dir: str = Field(default="processed_images", description="Directory for processed images")
    video_upload_dir: str = Field(default="uploaded_videos", description="Directory for uploaded videos")
    video_processed_dir: str = Field(default="processed_videos", description="Directory for processed videos")
    comfyui_input_dir: str = Field(default="./comfyui_input", description="ComfyUI input directory")
    image_template_dir: str = Field(default="./workflows/image", description="Image workflow templates directory")
    video_template_dir: str = Field(default="./workflows/video", description="Video workflow templates directory")
    
    # ComfyUI server configurations
    comfyui_servers: List[str] = Field(
        default=["127.0.0.1:8155", "127.0.0.1:8166"],
        description="List of ComfyUI server addresses"
    )
    
    # Timeout configurations
    health_check_timeout: int = Field(default=3, description="Health check request timeout in seconds")
    workflow_timeout: int = Field(default=600, description="Workflow execution timeout in seconds")
    video_workflow_timeout: int = Field(default=1200, description="Video workflow execution timeout in seconds")
    preload_timeout: int = Field(default=300, description="Preload timeout in seconds")
    
    # Concurrency configurations
    max_workers: int = Field(default=20, description="Maximum thread pool workers")
    health_check_interval: int = Field(default=5, description="Health check interval in seconds")
    max_error_count: int = Field(default=3, description="Maximum error count before marking server unavailable")
    
    # Placeholder configuration
    preload_placeholder_name: str = Field(default="preload_white.png", description="Placeholder image for preloading")
    
    # Server configuration
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=5000, description="Server port")
    workers: int = Field(default=4, description="Number of uvicorn workers")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    def ensure_directories(self):
        """Ensure all required directories exist"""
        directories = [
            self.upload_dir,
            self.processed_dir,
            self.video_upload_dir,
            self.video_processed_dir,
            self.comfyui_input_dir,
            self.image_template_dir,
            self.video_template_dir,
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)


# Global settings instance
settings = Settings()
