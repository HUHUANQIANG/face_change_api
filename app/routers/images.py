"""
Image processing API router
"""
import os
import json
import shutil
import base64
import io
from datetime import datetime
from fastapi import APIRouter, UploadFile, Form, HTTPException, File, Request
from fastapi.responses import JSONResponse
from PIL import Image

from app.config import settings
from app.models.schemas import ProcessImageResponse
from app.utils.file_utils import generate_unique_filename

router = APIRouter(tags=["processing"])

# Global references will be injected from main.py
load_balancer = None
tool_pool = None


def init_router(lb, tp):
    """Initialize router with load balancer and tool pool instances"""
    global load_balancer, tool_pool
    load_balancer = lb
    tool_pool = tp


@router.post('/process_image')
async def process_image(request: Request, image: UploadFile = File(...), template: str = Form(...), mode: str = Form('image')):
    """Process image request with automatic load balancing"""
    if image is None:
        raise HTTPException(status_code=400, detail='No image uploaded')
    
    # Check if template needs to be reloaded
    if template != tool_pool.current_template:
        if mode == 'video':
            template_dir = settings.video_template_dir
        else:
            template_dir = settings.image_template_dir
        
        template_path = os.path.join(template_dir, template)
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')
        
        with open(template_path, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
        
        if not workflow:
            raise HTTPException(status_code=500, detail='Failed to load workflow')
        
        tool_pool.load_workflow(workflow, template)
        print(f"📋 Template {template} loaded for processing")
    
    # Save uploaded image
    unique_filename = generate_unique_filename('png')
    local_path = os.path.join(settings.upload_dir, unique_filename)
    with open(local_path, 'wb') as f:
        shutil.copyfileobj(image.file, f)
    
    input_path = os.path.join(settings.comfyui_input_dir, unique_filename)
    try:
        shutil.copy(local_path, input_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to copy to input dir: {e}')
    
    # Get the most idle server tool
    try:
        comfy_tool = tool_pool.get_tool_for_request()
        server_addr = comfy_tool.server_address
        
        # Increment task count
        load_balancer.increment_task(server_addr)
        
        try:
            if not comfy_tool.workflow:
                raise HTTPException(status_code=500, detail='Workflow not loaded')
            
            run_result = comfy_tool.run_workflow_with_image(
                comfy_tool.workflow, 
                unique_filename, 
                timeout=settings.workflow_timeout
            )
            
            # Extract image result
            history_map = run_result.get('history')
            prompt_id = run_result.get('prompt_id')
            images_bytes = None
            
            if isinstance(history_map, dict) and prompt_id in history_map:
                prompt_hist = history_map.get(prompt_id, {})
                outputs = prompt_hist.get('outputs', {})
                for node_output in outputs.values():
                    imgs_meta = node_output.get('images')
                    if isinstance(imgs_meta, list) and len(imgs_meta) > 0 and isinstance(imgs_meta[0], dict):
                        first = imgs_meta[0]
                        images_bytes = comfy_tool._get_image_bytes(
                            first.get('filename'), 
                            first.get('subfolder'), 
                            first.get('type')
                        )
                        if images_bytes:
                            break
            
            if not images_bytes:
                return JSONResponse(content={
                    'status': 'success',
                    'original_image': local_path,
                    'processed_image_base64': None,
                    'processed_image_path': None,
                    'processed_image_url': None,
                    'server_used': server_addr,
                    'message': 'Workflow executed, no image output available'
                })
            
            img = Image.open(io.BytesIO(images_bytes))
            buffered = io.BytesIO()
            img.save(buffered, format='PNG')
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            processed_filename = f"processed_{generate_unique_filename('png')}"
            processed_path = os.path.join(settings.processed_dir, processed_filename)
            with open(processed_path, 'wb') as pf:
                pf.write(images_bytes)
            
            base_url = str(request.base_url).rstrip('/')
            processed_image_url = f"{base_url}/static/{processed_path}"
            
            return JSONResponse(content={
                'status': 'success',
                'original_image': local_path,
                'processed_image_base64': img_base64,
                'processed_image_path': processed_path,
                'processed_image_url': processed_image_url,
                'server_used': server_addr,
                'message': '处理成功！'
            })
        
        finally:
            # Decrement task count
            load_balancer.decrement_task(server_addr)
    
    except Exception as e:
        print(f"❌ run workflow error: {e}")
        raise HTTPException(status_code=500, detail=f'Processing error: {e}')
