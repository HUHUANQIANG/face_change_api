"""
File operation utilities
"""
import os
import shutil
import uuid
from datetime import datetime
from PIL import Image


def generate_unique_filename(extension: str = "png") -> str:
    """Generate a unique filename with timestamp and UUID"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_id = uuid.uuid4().hex[:8]
    return f"{timestamp}_{unique_id}.{extension}"


def save_uploaded_file(file_data, save_dir: str, filename: str) -> str:
    """
    Save uploaded file to directory
    
    Args:
        file_data: File object with read() method
        save_dir: Directory to save the file
        filename: Name of the file
        
    Returns:
        Full path to saved file
    """
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, filename)
    
    with open(file_path, 'wb') as f:
        shutil.copyfileobj(file_data, f)
    
    return file_path


def copy_file(src: str, dst: str) -> None:
    """
    Copy file from source to destination
    
    Args:
        src: Source file path
        dst: Destination file path
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(src, dst)


def create_placeholder_image(path: str, size: tuple = (16, 16), color: tuple = (255, 255, 255)) -> bool:
    """
    Create a placeholder image
    
    Args:
        path: Path to save the image
        size: Image size (width, height)
        color: RGB color tuple
        
    Returns:
        True if successful, False otherwise
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = Image.new('RGB', size, color)
        img.save(path)
        return True
    except Exception as e:
        print(f"⚠️ Failed to create placeholder image: {e}")
        return False


def list_files_with_extension(directory: str, extension: str) -> list:
    """
    List all files in directory with given extension
    
    Args:
        directory: Directory to search
        extension: File extension (e.g., '.json')
        
    Returns:
        List of filenames with the extension
    """
    if not os.path.exists(directory):
        return []
    
    return [f for f in os.listdir(directory) if f.endswith(extension)]
