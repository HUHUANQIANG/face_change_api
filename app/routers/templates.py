"""
Template management API router
"""
import os
import json
from fastapi import APIRouter, Form, HTTPException
from app.models.schemas import TemplatesResponse, LoadTemplateResponse
from app.config import settings
from app.utils.file_utils import list_files_with_extension

router = APIRouter(tags=["templates"])

# Global references will be injected from main.py
tool_pool = None


def init_router(tp):
    """Initialize router with tool pool instance"""
    global tool_pool
    tool_pool = tp


@router.get('/templates', response_model=TemplatesResponse)
def get_templates(mode: str = 'image'):
    """Get list of available templates"""
    if mode == 'video':
        template_dir = settings.video_template_dir
    else:
        template_dir = settings.image_template_dir
    
    if not os.path.exists(template_dir):
        return {'templates': [], 'mode': mode, 'message': f'Template directory for {mode} mode not found'}
    
    templates = list_files_with_extension(template_dir, '.json')
    if not templates:
        return {'templates': [], 'mode': mode, 'message': f'No templates found for {mode} mode'}
    
    return {'templates': templates, 'mode': mode}


@router.post('/load_template', response_model=LoadTemplateResponse)
def load_template(template: str = Form(...), mode: str = Form('image')):
    """Load template and preload on all servers"""
    if mode == 'video':
        template_dir = settings.video_template_dir
    else:
        template_dir = settings.image_template_dir
    
    template_path = os.path.join(template_dir, template)
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')
    
    # Load workflow
    with open(template_path, 'r', encoding='utf-8') as f:
        workflow = json.load(f)
    
    if not workflow:
        raise HTTPException(status_code=500, detail='Failed to load workflow')
    
    tool_pool.load_workflow(workflow, template)
    
    # Only preload in image mode
    if mode == 'image':
        # Parallel preload on all servers
        results = tool_pool.preload_all_servers(workflow, timeout=settings.preload_timeout)
        success_count = sum(1 for ok, _ in results.values() if ok)
        
        print(f"🔄 Preload results: {success_count}/{len(results)} servers succeeded")
        
        if success_count == 0:
            raise HTTPException(status_code=500, detail=f'Preload failed on all servers: {results}')
        
        message_text = f'Workflow {template} loaded and preloaded on {success_count}/{len(results)} servers'
        info = str(results)
    else:
        info = "No preload for video mode"
        print(f"🔄 video mode: {info}")
        message_text = f'Workflow {template} loaded for {mode} mode (no preload)'
    
    return {'status': 'success', 'message': message_text, 'info': info, 'mode': mode}
