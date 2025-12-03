import os
import uuid
import shutil
import json
import requests
import time
import websocket
from datetime import datetime
from fastapi import FastAPI, UploadFile, Form, HTTPException, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
import io
import urllib.request
import urllib.parse
import base64
import copy

# ----------------------------
# 全局配置（请根据实际环境调整）
# ----------------------------
UPLOAD_DIR = "uploaded_images"
PROCESSED_DIR = "processed_images"
VIDEO_UPLOAD_DIR = "uploaded_videos"
VIDEO_PROCESSED_DIR = "processed_videos"
COMFYUI_SERVER = "127.0.0.1:8155"
COMFYUI_INPUT_DIR = "/home/huhq/comfy/ComfyUI/input/"  # ComfyUI 可读的 input 目录
IMAGE_TEMPLATE_DIR = "./workflows/image"  # 图片处理模板目录
VIDEO_TEMPLATE_DIR = "./workflows/video"  # 视频处理模板目录
PRELOAD_PLACEHOLDER_NAME = 'preload_white.png'  # 预加载时用的白色占位图

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(VIDEO_UPLOAD_DIR, exist_ok=True)
os.makedirs(VIDEO_PROCESSED_DIR, exist_ok=True)
os.makedirs(COMFYUI_INPUT_DIR, exist_ok=True)
os.makedirs(IMAGE_TEMPLATE_DIR, exist_ok=True)
os.makedirs(VIDEO_TEMPLATE_DIR, exist_ok=True)

# ----------------------------
# ComfyUI 通信封装类（改进版）
# ----------------------------
class ComfyUITool:
    def __init__(self, server_address, working_dir):
        self.server_address = server_address
        self.working_dir = working_dir
        self.client_id = str(uuid.uuid4())
        self.workflow = None  # loaded original workflow JSON
        self.preloaded = False

    def _load_workflow(self, workflow_file):
        try:
            with open(workflow_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 无法加载工作流文件: {e}")
            return None

    def _queue_prompt(self, workflow) -> dict:
        """Submit prompt to ComfyUI using /prompt endpoint (wrapper)."""
        payload = {"prompt": workflow, "client_id": self.client_id}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def _get_history(self, prompt_id):
        with urllib.request.urlopen(f"http://{self.server_address}/history/{prompt_id}") as response:
            return json.loads(response.read())

    def _get_image_bytes(self, filename, subfolder, folder_type):
        params = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type
        })
        url = f"http://{self.server_address}/view?{params}"
        with urllib.request.urlopen(url) as response:
            return response.read()

    def _wait_for_prompt_exec(self, prompt_id, timeout=120):
        """Open websocket and wait until executing message with node==None and matching prompt_id."""
        ws = websocket.create_connection(f"ws://{self.server_address}/ws?clientId={self.client_id}")
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                out = ws.recv()
                if isinstance(out, str):
                    msg = json.loads(out)
                    mtype = msg.get('type')
                    if mtype == 'progress':
                        data = msg.get('data', {})
                        print(f"📈 progress: {data.get('value')}/{data.get('max')}")
                    elif mtype == 'executing':
                        data = msg.get('data', {})
                        # ComfyUI sends executing with node=None when finished
                        if data.get('node') is None and data.get('prompt_id') == prompt_id:
                            print(f"✅ prompt {prompt_id} executed")
                            return True
            return False
        finally:
            try:
                ws.close()
            except:
                pass

    def preload_full_workflow(self, workflow: dict, timeout=300) -> tuple:
        """
        Submit the *entire* workflow to ComfyUI, but replace all LoadImage inputs with a white placeholder image.
        Do NOT retrieve or display outputs. This causes ComfyUI to execute the workflow and load models into GPU.
        Returns (success: bool, info: str)
        """
        try:
            if not workflow:
                return False, 'empty workflow'

            # ensure placeholder exists in COMFYUI_INPUT_DIR
            placeholder_path = os.path.join(COMFYUI_INPUT_DIR, PRELOAD_PLACEHOLDER_NAME)
            if not os.path.exists(placeholder_path):
                try:
                    img = Image.new('RGB', (16, 16), (255, 255, 255))
                    img.save(placeholder_path)
                    print(f"✅ created placeholder at {placeholder_path}")
                except Exception as e:
                    print(f"⚠️ failed creating placeholder: {e}")
                    return False, f"failed to create placeholder: {e}"

            # deep copy workflow and replace LoadImage nodes' inputs with placeholder filename
            wf_copy = copy.deepcopy(workflow)
            for nid, node in wf_copy.items():
                if node.get('class_type') == 'LoadImage':
                    inputs = node.setdefault('inputs', {})
                    # find image input key (commonly 'image')
                    for k, v in list(inputs.items()):
                        if isinstance(v, str) and (v.endswith('.png') or v.endswith('.jpg') or 'pasted/' in v or 'input' in v):
                            inputs[k] = PRELOAD_PLACEHOLDER_NAME
                        elif isinstance(v, list):
                            # sometimes image is inside a list like ['pasted/image.png', 0]
                            new_list = []
                            changed = False
                            for item in v:
                                if isinstance(item, str) and (item.endswith('.png') or item.endswith('.jpg') or 'pasted/' in item):
                                    new_list.append(PRELOAD_PLACEHOLDER_NAME)
                                    changed = True
                                else:
                                    new_list.append(item)
                            if changed:
                                inputs[k] = new_list
                    # if no image key found, set common key
                    if 'image' not in inputs:
                        inputs['image'] = PRELOAD_PLACEHOLDER_NAME

            # Submit the full workflow (with placeholder) — this should execute loaders and other nodes
            print(f"🚀 submitting full workflow for preload (node count={len(wf_copy)})")
            resp = self._queue_prompt(wf_copy)
            prompt_id = resp.get('prompt_id') or resp.get('id') or resp.get('request_id')
            if not prompt_id:
                return False, f"no prompt id returned: {resp}"

            ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
            if not ok:
                return False, f"preload timeout or ws error, resp={resp}"

            # mark as preloaded for this workflow
            self.preloaded = True
            return True, f"preloaded prompt_id={prompt_id}"
        except Exception as e:
            return False, f"exception: {e}"

    def run_workflow_with_image(self, workflow: dict, image_filename: str, timeout=300) -> dict:
        """
        Submit the workflow replacing LoadImage nodes with the provided image_filename. Wait for completion and
        return the history outputs (so caller can fetch images if desired).
        """
        wf_copy = copy.deepcopy(workflow)
        target_node_id = "10"
        node = wf_copy.get(target_node_id)
        if node and node.get('class_type') == 'LoadImage':
            inputs = node.setdefault('inputs', {})
            for k, v in list(inputs.items()):
                if isinstance(v, str) and (v.endswith('.png') or v.endswith('.jpg') or 'pasted/' in v or 'input' in v):
                    inputs[k] = image_filename
                elif isinstance(v, list):
                    new_list = []
                    changed = False
                    for item in v:
                        if isinstance(item, str) and (item.endswith('.png') or item.endswith('.jpg') or 'pasted/' in item):
                            new_list.append(image_filename)
                            changed = True
                        else:
                            new_list.append(item)
                    if changed:
                        inputs[k] = new_list
            if 'image' not in inputs:
                inputs['image'] = image_filename
        
        resp = self._queue_prompt(wf_copy)
        prompt_id = resp.get('prompt_id') or resp.get('id') or resp.get('request_id')
        if not prompt_id:
            raise RuntimeError(f"no prompt id returned: {resp}")

        ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
        if not ok:
            raise RuntimeError(f"workflow run timeout or ws error, resp={resp}")

        # fetch history and return it to caller
        history = self._get_history(prompt_id)
        return {'prompt_id': prompt_id, 'history': history}

    def run_workflow_with_video(self, workflow: dict, video_filename: str, timeout=600, target_node_id: str = "2") -> dict:
        """
        Submit the workflow replacing LoadVideo nodes with the provided video_filename. Wait for completion and
        return the history outputs (so caller can fetch videos if desired).
        Only replace the specified target video node (default id "2").
        """
        wf_copy = copy.deepcopy(workflow)
        # 仅替换指定节点（默认 2），避免改动背景等其他视频节点
        for nid, node in wf_copy.items():
            if str(nid) != str(target_node_id):
                continue
            if node.get('class_type') in ['LoadVideo', 'VHS_LoadVideo', 'LoadVideoPath']:
                inputs = node.setdefault('inputs', {})
                if 'video' in inputs:
                    inputs['video'] = video_filename
                elif 'video_path' in inputs:
                    inputs['video_path'] = video_filename
                else:
                    inputs['video'] = video_filename
                break
        
        resp = self._queue_prompt(wf_copy)
        prompt_id = resp.get('prompt_id') or resp.get('id') or resp.get('request_id')
        if not prompt_id:
            raise RuntimeError(f"no prompt id returned: {resp}")

        ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
        if not ok:
            raise RuntimeError(f"workflow run timeout or ws error, resp={resp}")

        # fetch history and return it to caller
        history = self._get_history(prompt_id)
        return {'prompt_id': prompt_id, 'history': history}

    def free_memory(self):
        try:
            response = requests.post(f"http://{self.server_address}/free", json={}, timeout=5)
            if response.status_code == 200:
                return True, "显存已释放"
        except Exception:
            pass
        try:
            self._queue_prompt({})
            return True, "已通过空任务触发清理"
        except Exception as e:
            return False, f"显存释放失败: {e}"


# ----------------------------
# FastAPI 应用
# ----------------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加静态文件支持
app.mount("/static", StaticFiles(directory="."), name="static")

comfy = ComfyUITool(COMFYUI_SERVER, working_dir=os.getcwd())
current_template = None


@app.get('/templates')
def get_templates(mode: str = 'image'):
    """获取模板列表，支持按模式（image/video）筛选"""
    if mode == 'video':
        template_dir = VIDEO_TEMPLATE_DIR
    else:
        template_dir = IMAGE_TEMPLATE_DIR
    
    if not os.path.exists(template_dir):
        return {'templates': [], 'message': f'Template directory for {mode} mode not found'}
    
    templates = [f for f in os.listdir(template_dir) if f.endswith('.json')]
    if not templates:
        return {'templates': [], 'message': f'No templates found for {mode} mode'}
    
    return {'templates': templates, 'mode': mode}


@app.post('/load_template')
def load_template(template: str = Form(...), mode: str = Form('image')):
    """加载模板，支持指定模式（image/video）"""
    global current_template
    
    if mode == 'video':
        template_dir = VIDEO_TEMPLATE_DIR
    else:
        template_dir = IMAGE_TEMPLATE_DIR
    
    template_path = os.path.join(template_dir, template)
    if not os.path.exists(template_path):
        raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

    # free memory first
    success, message = comfy.free_memory()
    print(f"🧹 {message}")

    workflow = comfy._load_workflow(template_path)
    if not workflow:
        raise HTTPException(status_code=500, detail='Failed to load workflow')

    comfy.workflow = workflow

    # 只在图片模式下进行预加载，视频模式下不预加载
    if mode == 'image':
        # 关键点：在这里做 **完整工作流预加载**，将所有 LoadImage 替换为白色占位图
        ok, info = comfy.preload_full_workflow(workflow, timeout=300)
        print(f"🔄 preload result: {ok}, {info}")
        if not ok:
            raise HTTPException(status_code=500, detail=f'Preload failed: {info}')
        message_text = f'Workflow {template} loaded and preloaded for {mode} mode'
    else:
        info = "No preload for video mode"
        print(f"🔄 video mode: {info}")
        message_text = f'Workflow {template} loaded for {mode} mode (no preload)'

    current_template = template
    return {'status': 'success', 'message': message_text, 'info': info, 'mode': mode}


@app.post('/process_image')
async def process_image(request: Request, image: UploadFile = File(...), template: str = Form(...), mode: str = Form('image')):
    global current_template
    if image is None:
        raise HTTPException(status_code=400, detail='No image uploaded')

    # 只有当模板不同时才重新加载模板，但不在这里预加载
    if template != current_template:
        # 只加载模板，不重复预加载
        if mode == 'video':
            template_dir = VIDEO_TEMPLATE_DIR
        else:
            template_dir = IMAGE_TEMPLATE_DIR
        
        template_path = os.path.join(template_dir, template)
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

        # free memory first
        success, message = comfy.free_memory()
        print(f"🧹 {message}")

        workflow = comfy._load_workflow(template_path)
        if not workflow:
            raise HTTPException(status_code=500, detail='Failed to load workflow')

        comfy.workflow = workflow
        current_template = template
        print(f"📋 Template {template} loaded for processing (no preload)")

    # save uploaded image locally and copy to comfy input
    unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    local_path = os.path.join(UPLOAD_DIR, unique_filename)
    with open(local_path, 'wb') as f:
        shutil.copyfileobj(image.file, f)

    input_path = os.path.join(COMFYUI_INPUT_DIR, unique_filename)
    try:
        shutil.copy(local_path, input_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to copy to input dir: {e}')

    if not comfy.workflow:
        raise HTTPException(status_code=500, detail='Workflow not loaded')

    # run full workflow with this image (replacing LoadImage inputs)
    try:
        run_result = comfy.run_workflow_with_image(comfy.workflow, unique_filename, timeout=600)
        # extract first image output if any
        history_map = run_result.get('history')
        prompt_id = run_result.get('prompt_id')
        # Only read outputs.images from the prompt's history
        images_bytes = None
        if isinstance(history_map, dict) and prompt_id in history_map:
            prompt_hist = history_map.get(prompt_id, {})
            outputs = prompt_hist.get('outputs', {})
            for node_output in outputs.values():
                imgs_meta = node_output.get('images')
                if isinstance(imgs_meta, list) and len(imgs_meta) > 0 and isinstance(imgs_meta[0], dict):
                    first = imgs_meta[0]
                    images_bytes = comfy._get_image_bytes(first.get('filename'), first.get('subfolder'), first.get('type'))
                    if images_bytes:
                        break

        if not images_bytes:
            # no image produced or couldn't fetch; still return success
            return JSONResponse(content={
                'status': 'success',
                'original_image': local_path,
                'processed_image_base64': None,
                'processed_image_path': None,
                'processed_image_url': None,
                'message': 'Workflow executed, no image output available (or not fetched)'
            })

        img = Image.open(io.BytesIO(images_bytes))
        buffered = io.BytesIO()
        img.save(buffered, format='PNG')
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

        processed_filename = f"processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        processed_path = os.path.join(PROCESSED_DIR, processed_filename)
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
            'message': '处理成功！'
        })

    except Exception as e:
        print(f"❌ run workflow error: {e}")
        raise HTTPException(status_code=500, detail=f'Processing error: {e}')


@app.post('/process_video')
async def process_video(request: Request, video: UploadFile = File(...), template: str = Form(...), mode: str = Form('video')):
    global current_template
    if video is None:
        raise HTTPException(status_code=400, detail='No video uploaded')

    # 检查视频文件类型
    if not video.filename.lower().endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm')):
        raise HTTPException(status_code=400, detail='Unsupported video format')

    # 只有当模板不同时才重新加载模板，但不在这里预加载
    if template != current_template:
        # 只加载模板，不重复预加载
        if mode == 'video':
            template_dir = VIDEO_TEMPLATE_DIR
        else:
            template_dir = IMAGE_TEMPLATE_DIR
        
        template_path = os.path.join(template_dir, template)
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

        # free memory first
        success, message = comfy.free_memory()
        print(f"🧹 {message}")

        workflow = comfy._load_workflow(template_path)
        if not workflow:
            raise HTTPException(status_code=500, detail='Failed to load workflow')

        comfy.workflow = workflow
        current_template = template
        print(f"📋 Template {template} loaded for processing (no preload)")

    # save uploaded video locally and copy to comfy input
    unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
    local_path = os.path.join(VIDEO_UPLOAD_DIR, unique_filename)
    with open(local_path, 'wb') as f:
        shutil.copyfileobj(video.file, f)

    input_path = os.path.join(COMFYUI_INPUT_DIR, unique_filename)
    try:
        shutil.copy(local_path, input_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to copy to input dir: {e}')

    if not comfy.workflow:
        raise HTTPException(status_code=500, detail='Workflow not loaded')

    # run full workflow with this video (replacing LoadVideo inputs)
    try:
        run_result = comfy.run_workflow_with_video(comfy.workflow, unique_filename, timeout=1200)
        # extract first video output if any
        history_map = run_result.get('history')
        prompt_id = run_result.get('prompt_id')
        
        video_bytes = None
        if isinstance(history_map, dict) and prompt_id in history_map:
            prompt_hist = history_map.get(prompt_id, {})
            outputs = prompt_hist.get('outputs', {})
            for node_output in outputs.values():
                # 查找视频输出
                videos_meta = node_output.get('videos') or node_output.get('gifs')
                if isinstance(videos_meta, list) and len(videos_meta) > 0 and isinstance(videos_meta[0], dict):
                    first = videos_meta[0]
                    video_bytes = comfy._get_image_bytes(first.get('filename'), first.get('subfolder'), first.get('type'))
                    if video_bytes:
                        break

        if not video_bytes:
            # no video produced or couldn't fetch; still return success
            return JSONResponse(content={
                'status': 'success',
                'original_video': local_path,
                'processed_video_base64': None,
                'processed_video_path': None,
                'message': 'Workflow executed, no video output available (or not fetched)'
            })

        # 保存处理后的视频
        processed_filename = f"processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
        processed_path = os.path.join(VIDEO_PROCESSED_DIR, processed_filename)
        with open(processed_path, 'wb') as pf:
            pf.write(video_bytes)

        # 将视频转换为base64（注意：视频文件可能很大）
        video_base64 = base64.b64encode(video_bytes).decode('utf-8')

        base_url = str(request.base_url).rstrip('/')
        processed_video_url = f"{base_url}/static/{processed_path}"

        return JSONResponse(content={
            'status': 'success',
            'original_video': local_path,
            'processed_video_base64': video_base64,
            'processed_video_path': processed_path,
            'processed_video_url': processed_video_url,
            'message': '视频处理成功！'
        })

    except Exception as e:
        print(f"❌ run video workflow error: {e}")
        raise HTTPException(status_code=500, detail=f'Video processing error: {e}')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
