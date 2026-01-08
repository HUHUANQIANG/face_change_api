import os
import uuid
import shutil
import json
import requests
import time
import websocket
import asyncio
import random
import threading
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, field
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
from concurrent.futures import ThreadPoolExecutor

# ----------------------------
# 全局配置（请根据实际环境调整）
# ----------------------------
UPLOAD_DIR = "uploaded_images"
PROCESSED_DIR = "processed_images"
VIDEO_UPLOAD_DIR = "uploaded_videos"
VIDEO_PROCESSED_DIR = "processed_videos"

# 多个 ComfyUI 服务器配置（可以根据实际情况添加更多）
COMFYUI_SERVERS = [
    "127.0.0.1:8155",
    "127.0.0.1:8166",  # 取消注释以添加更多服务器
    # "127.0.0.1:8157",
    # "127.0.0.1:8158",
]

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
# ComfyUI 服务器状态数据类
# ----------------------------
@dataclass
class ComfyUIServerStatus:
    server_address: str
    is_available: bool = True
    queue_remaining: int = 0
    queue_pending: int = 0
    current_tasks: int = 0  # 当前正在处理的任务数
    last_check_time: float = 0
    error_count: int = 0
    
    @property
    def total_load(self) -> int:
        """计算总负载 = 队列中的任务 + 当前正在处理的任务"""
        return self.queue_remaining + self. queue_pending + self.current_tasks


# ----------------------------
# ComfyUI 负载均衡器
# ----------------------------
class ComfyUILoadBalancer: 
    def __init__(self, server_addresses: List[str]):
        self.servers:  Dict[str, ComfyUIServerStatus] = {}
        self.lock = threading.Lock()
        
        for addr in server_addresses: 
            self.servers[addr] = ComfyUIServerStatus(server_address=addr)
        
        # 启动后台健康检查线程
        self._start_health_check()
    
    def _start_health_check(self):
        """启动后台线程定期检查服务器状态"""
        def check_loop():
            while True:
                for addr in list(self.servers. keys()):
                    self._update_server_status(addr)
                time.sleep(5)  # 每5秒检查一次
        
        thread = threading.Thread(target=check_loop, daemon=True)
        thread.start()
    
    def _update_server_status(self, server_address: str):
        """更新单个服务器的状态"""
        try:
            url = f"http://{server_address}/queue"
            response = requests.get(url, timeout=3)
            
            if response.status_code == 200:
                data = response.json()
                with self.lock:
                    status = self.servers[server_address]
                    # ComfyUI /queue 返回格式：
                    # {"queue_running": [... ], "queue_pending": [...]}
                    queue_running = data.get('queue_running', [])
                    queue_pending = data. get('queue_pending', [])
                    
                    status.queue_remaining = len(queue_running)
                    status.queue_pending = len(queue_pending)
                    status.is_available = True
                    status.last_check_time = time.time()
                    status.error_count = 0
                    
                    # print(f"✅ Server {server_address}:  running={len(queue_running)}, pending={len(queue_pending)}")
            else:
                self._mark_server_error(server_address)
        except Exception as e:
            print(f"⚠️ Health check failed for {server_address}: {e}")
            self._mark_server_error(server_address)
    
    def _mark_server_error(self, server_address:  str):
        """标记服务器出错"""
        with self.lock:
            status = self.servers[server_address]
            status.error_count += 1
            if status.error_count >= 3:  # 连续3次失败则标记为不可用
                status.is_available = False
                print(f"❌ Server {server_address} marked as unavailable")
    
    def get_best_server(self) -> Optional[str]:
        """获取最空闲的服务器地址"""
        with self.lock:
            available_servers = [
                (addr, status) for addr, status in self.servers.items()
                if status.is_available
            ]
            if not available_servers:
                return list(self.servers.keys())[0] if self.servers else None

            random.shuffle(available_servers)  # 避免负载相同时时刻选第一台
            available_servers.sort(key=lambda x: x[1].total_load)
            best_server = available_servers[0][0]
            print(f"🎯 Selected server: {best_server} (load: {self.servers[best_server].total_load})")
            return best_server
    
    def increment_task(self, server_address: str):
        """增加服务器当前任务计数"""
        with self.lock:
            if server_address in self.servers:
                self.servers[server_address]. current_tasks += 1
    
    def decrement_task(self, server_address: str):
        """减少服务器当前任务计数"""
        with self.lock:
            if server_address in self.servers:
                self.servers[server_address].current_tasks = max(0, self.servers[server_address].current_tasks - 1)
    
    def get_all_status(self) -> Dict:
        """获取所有服务器状态"""
        with self.lock:
            return {
                addr: {
                    'is_available': status. is_available,
                    'queue_remaining': status.queue_remaining,
                    'queue_pending': status.queue_pending,
                    'current_tasks':  status.current_tasks,
                    'total_load': status. total_load,
                    'error_count': status. error_count
                }
                for addr, status in self.servers.items()
            }
    
    def add_server(self, server_address: str):
        """动态添加新服务器"""
        with self.lock:
            if server_address not in self.servers:
                self.servers[server_address] = ComfyUIServerStatus(server_address=server_address)
                print(f"➕ Added new server: {server_address}")
    
    def remove_server(self, server_address: str):
        """动态移除服务器"""
        with self.lock:
            if server_address in self. servers:
                del self.servers[server_address]
                print(f"➖ Removed server: {server_address}")


# ----------------------------
# ComfyUI 通信封装类（支持负载均衡）
# ----------------------------
class ComfyUITool:
    def __init__(self, server_address: str, working_dir: str):
        self.server_address = server_address
        self. working_dir = working_dir
        self.client_id = str(uuid. uuid4())
        self.workflow = None
        self. preloaded = False

    def _load_workflow(self, workflow_file):
        try:
            with open(workflow_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 无法加载工作流文件: {e}")
            return None

    def _queue_prompt(self, workflow) -> dict:
        """Submit prompt to ComfyUI using /prompt endpoint (wrapper)."""
        payload = {"prompt": workflow, "client_id":  self.client_id}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp. read())

    def _get_history(self, prompt_id):
        with urllib.request.urlopen(f"http://{self.server_address}/history/{prompt_id}") as response:
            return json.loads(response. read())

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
                        print(f"📈 [{self.server_address}] progress: {data.get('value')}/{data.get('max')}")
                    elif mtype == 'executing':
                        data = msg.get('data', {})
                        if data.get('node') is None and data.get('prompt_id') == prompt_id:
                            print(f"✅ [{self. server_address}] prompt {prompt_id} executed")
                            return True
            return False
        finally:
            try:
                ws. close()
            except:
                pass

    def preload_full_workflow(self, workflow:  dict, timeout=300) -> tuple: 
        """预加载工作流"""
        try:
            if not workflow: 
                return False, 'empty workflow'

            placeholder_path = os.path. join(COMFYUI_INPUT_DIR, PRELOAD_PLACEHOLDER_NAME)
            if not os.path.exists(placeholder_path):
                try:
                    img = Image.new('RGB', (16, 16), (255, 255, 255))
                    img.save(placeholder_path)
                    print(f"✅ created placeholder at {placeholder_path}")
                except Exception as e:
                    print(f"⚠️ failed creating placeholder: {e}")
                    return False, f"failed to create placeholder: {e}"

            wf_copy = copy.deepcopy(workflow)
            for nid, node in wf_copy.items():
                if node.get('class_type') == 'LoadImage':
                    inputs = node.setdefault('inputs', {})
                    for k, v in list(inputs.items()):
                        if isinstance(v, str) and (v.endswith('.png') or v.endswith('. jpg') or 'pasted/' in v or 'input' in v):
                            inputs[k] = PRELOAD_PLACEHOLDER_NAME
                        elif isinstance(v, list):
                            new_list = []
                            changed = False
                            for item in v: 
                                if isinstance(item, str) and (item.endswith('.png') or item.endswith('. jpg') or 'pasted/' in item):
                                    new_list.append(PRELOAD_PLACEHOLDER_NAME)
                                    changed = True
                                else:
                                    new_list.append(item)
                            if changed: 
                                inputs[k] = new_list
                    if 'image' not in inputs:
                        inputs['image'] = PRELOAD_PLACEHOLDER_NAME

            print(f"🚀 [{self.server_address}] submitting full workflow for preload (node count={len(wf_copy)})")
            resp = self._queue_prompt(wf_copy)
            prompt_id = resp. get('prompt_id') or resp.get('id') or resp.get('request_id')
            if not prompt_id: 
                return False, f"no prompt id returned: {resp}"

            ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
            if not ok:
                return False, f"preload timeout or ws error, resp={resp}"

            self.preloaded = True
            return True, f"preloaded prompt_id={prompt_id}"
        except Exception as e:
            return False, f"exception:  {e}"

    def run_workflow_with_image(self, workflow: dict, image_filename: str, timeout=300) -> dict:
        """
        Submit the workflow replacing LoadImage nodes with the provided image_filename. Wait for completion and
        return the history outputs (so caller can fetch images if desired).
        """
        wf_copy = copy.deepcopy(workflow)
        target_node_id = "10"
        node = wf_copy.get(target_node_id)
        replaced = False

        # 优先尝试替换 ID 为 10 的节点
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
            replaced = True

        # 如果没有找到 ID 10 或者它不是 LoadImage，则查找第一个 LoadImage 节点进行替换
        if not replaced:
            for nid, node in wf_copy.items():
                if node.get('class_type') == 'LoadImage':
                    inputs = node.setdefault('inputs', {})
                    if 'image' not in inputs:
                        inputs['image'] = image_filename
                    else:
                        inputs['image'] = image_filename
                    print(f"ℹ️ Auto-detected and replaced LoadImage node at ID {nid}")
                    replaced = True
                    break
        
        if not replaced:
            print("⚠️ No LoadImage node found to replace!")

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

    def run_workflow_with_video(self, workflow: dict, video_filename: str, timeout=600, target_node_id:  str = "2") -> dict:
        """运行视频工作流"""
        wf_copy = copy.deepcopy(workflow)
        for nid, node in wf_copy. items():
            if str(nid) != str(target_node_id):
                continue
            if node. get('class_type') in ['LoadVideo', 'VHS_LoadVideo', 'LoadVideoPath']:
                inputs = node.setdefault('inputs', {})
                if 'video' in inputs:
                    inputs['video'] = video_filename
                elif 'video_path' in inputs: 
                    inputs['video_path'] = video_filename
                else:
                    inputs['video'] = video_filename
                break
        
        resp = self._queue_prompt(wf_copy)
        prompt_id = resp. get('prompt_id') or resp.get('id') or resp.get('request_id')
        if not prompt_id: 
            raise RuntimeError(f"no prompt id returned: {resp}")

        ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
        if not ok: 
            raise RuntimeError(f"workflow run timeout or ws error, resp={resp}")

        history = self._get_history(prompt_id)
        return {'prompt_id':  prompt_id, 'history': history}

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
# ComfyUI 工具池管理器
# ----------------------------
class ComfyUIToolPool:
    def __init__(self, load_balancer: ComfyUILoadBalancer):
        self.load_balancer = load_balancer
        self.tools: Dict[str, ComfyUITool] = {}
        self.lock = threading.Lock()
        self.workflow = None
        self.current_template = None
        
        # 为每个服务器创建工具实例
        for server_addr in load_balancer.servers. keys():
            self.tools[server_addr] = ComfyUITool(server_addr, working_dir=os.getcwd())
    
    def get_tool_for_request(self) -> ComfyUITool:
        """获取最适合处理当前请求的工具实例"""
        best_server = self. load_balancer. get_best_server()
        if not best_server: 
            raise RuntimeError("No available ComfyUI servers")
        
        with self.lock:
            if best_server not in self.tools:
                self. tools[best_server] = ComfyUITool(best_server, working_dir=os.getcwd())
            
            tool = self.tools[best_server]
            # 同步工作流到该工具
            if self.workflow:
                tool. workflow = self.workflow
            
            return tool
    
    def load_workflow(self, workflow: dict, template_name: str):
        """加载工作流到所有工具"""
        with self.lock:
            self.workflow = workflow
            self.current_template = template_name
            for tool in self.tools. values():
                tool.workflow = workflow
    
    def preload_all_servers(self, workflow: dict, timeout=300) -> Dict[str, tuple]:
        """在所有服务器上预加载工作流"""
        results = {}
        
        def preload_on_server(server_addr: str):
            tool = self. tools. get(server_addr)
            if tool:
                return server_addr, tool. preload_full_workflow(workflow, timeout)
            return server_addr, (False, "No tool instance")
        
        # 使用线程池并行预加载
        with ThreadPoolExecutor(max_workers=len(self.tools)) as executor:
            futures = [executor.submit(preload_on_server, addr) for addr in self.tools.keys()]
            for future in futures: 
                addr, result = future.result()
                results[addr] = result
        
        return results
    
    def add_server(self, server_address: str):
        """动态添加新服务器"""
        self.load_balancer.add_server(server_address)
        with self.lock:
            if server_address not in self.tools:
                tool = ComfyUITool(server_address, working_dir=os.getcwd())
                if self.workflow:
                    tool.workflow = self.workflow
                self.tools[server_address] = tool


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

static_dir = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# 初始化负载均衡器和工具池
load_balancer = ComfyUILoadBalancer(COMFYUI_SERVERS)
tool_pool = ComfyUIToolPool(load_balancer)

# 线程池用于并发处理
executor = ThreadPoolExecutor(max_workers=20)


@app.get('/servers/status')
def get_servers_status():
    """获取所有 ComfyUI 服务器的状态"""
    return {
        'servers': load_balancer.get_all_status(),
        'total_servers': len(load_balancer.servers)
    }


@app.post('/servers/add')
def add_server(server_address: str = Form(...)):
    """动态添加新的 ComfyUI 服务器"""
    tool_pool.add_server(server_address)
    return {'status': 'success', 'message': f'Server {server_address} added'}


@app. post('/servers/remove')
def remove_server(server_address: str = Form(... )):
    """动态移除 ComfyUI 服务器"""
    load_balancer.remove_server(server_address)
    return {'status': 'success', 'message': f'Server {server_address} removed'}


@app. get('/templates')
def get_templates(mode: str = 'image'):
    """获取模板列表"""
    if mode == 'video':
        template_dir = VIDEO_TEMPLATE_DIR
    else:
        template_dir = IMAGE_TEMPLATE_DIR
    
    if not os.path. exists(template_dir):
        return {'templates': [], 'message': f'Template directory for {mode} mode not found'}
    
    templates = [f for f in os.listdir(template_dir) if f.endswith('.json')]
    if not templates:
        return {'templates': [], 'message': f'No templates found for {mode} mode'}
    
    return {'templates': templates, 'mode': mode}


@app.post('/load_template')
def load_template(template:  str = Form(...), mode: str = Form('image')):
    """加载模板并在所有服务器上预加载"""
    if mode == 'video':
        template_dir = VIDEO_TEMPLATE_DIR
    else:
        template_dir = IMAGE_TEMPLATE_DIR
    
    template_path = os. path.join(template_dir, template)
    if not os.path. exists(template_path):
        raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

    # 加载工作流
    with open(template_path, 'r', encoding='utf-8') as f:
        workflow = json. load(f)
    
    if not workflow:
        raise HTTPException(status_code=500, detail='Failed to load workflow')

    tool_pool.load_workflow(workflow, template)

    # 只在图片模式下进行预加载
    if mode == 'image':
        # 在所有服务器上并行预加载
        results = tool_pool.preload_all_servers(workflow, timeout=300)
        success_count = sum(1 for ok, _ in results.values() if ok)
        
        print(f"🔄 Preload results: {success_count}/{len(results)} servers succeeded")
        
        if success_count == 0:
            raise HTTPException(status_code=500, detail=f'Preload failed on all servers:  {results}')
        
        message_text = f'Workflow {template} loaded and preloaded on {success_count}/{len(results)} servers'
        info = str(results)
    else:
        info = "No preload for video mode"
        print(f"🔄 video mode: {info}")
        message_text = f'Workflow {template} loaded for {mode} mode (no preload)'

    return {'status': 'success', 'message': message_text, 'info': info, 'mode': mode}


@app.post('/process_image')
async def process_image(request: Request, image:  UploadFile = File(...), template: str = Form(... ), mode: str = Form('image')):
    """处理图片请求 - 自动负载均衡"""
    if image is None:
        raise HTTPException(status_code=400, detail='No image uploaded')

    # 检查模板是否需要重新加载
    if template != tool_pool.current_template:
        if mode == 'video': 
            template_dir = VIDEO_TEMPLATE_DIR
        else:
            template_dir = IMAGE_TEMPLATE_DIR
        
        template_path = os.path. join(template_dir, template)
        if not os.path.exists(template_path):
            raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

        with open(template_path, 'r', encoding='utf-8') as f:
            workflow = json. load(f)
        
        if not workflow:
            raise HTTPException(status_code=500, detail='Failed to load workflow')

        tool_pool.load_workflow(workflow, template)
        print(f"📋 Template {template} loaded for processing")

    # 保存上传的图片
    unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[: 8]}.png"
    local_path = os. path.join(UPLOAD_DIR, unique_filename)
    with open(local_path, 'wb') as f:
        shutil.copyfileobj(image.file, f)

    input_path = os.path.join(COMFYUI_INPUT_DIR, unique_filename)
    try:
        shutil.copy(local_path, input_path)
    except Exception as e: 
        raise HTTPException(status_code=500, detail=f'Failed to copy to input dir: {e}')

    # 获取最空闲的服务器工具
    try:
        comfy_tool = tool_pool. get_tool_for_request()
        server_addr = comfy_tool.server_address
        
        # 增加任务计数
        load_balancer.increment_task(server_addr)
        
        try:
            if not comfy_tool. workflow:
                raise HTTPException(status_code=500, detail='Workflow not loaded')

            run_result = comfy_tool.run_workflow_with_image(comfy_tool.workflow, unique_filename, timeout=600)
            
            # 提取图片结果
            history_map = run_result.get('history')
            prompt_id = run_result.get('prompt_id')
            images_bytes = None
            
            if isinstance(history_map, dict) and prompt_id in history_map: 
                prompt_hist = history_map. get(prompt_id, {})
                outputs = prompt_hist. get('outputs', {})
                for node_output in outputs.values():
                    imgs_meta = node_output. get('images')
                    if isinstance(imgs_meta, list) and len(imgs_meta) > 0 and isinstance(imgs_meta[0], dict):
                        first = imgs_meta[0]
                        images_bytes = comfy_tool._get_image_bytes(first. get('filename'), first.get('subfolder'), first.get('type'))
                        if images_bytes: 
                            break

            if not images_bytes: 
                return JSONResponse(content={
                    'status': 'success',
                    'original_image': local_path,
                    'processed_image_base64': None,
                    'processed_image_path':  None,
                    'processed_image_url': None,
                    'server_used': server_addr,
                    'message': 'Workflow executed, no image output available'
                })

            img = Image.open(io. BytesIO(images_bytes))
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
                'original_image':  local_path,
                'processed_image_base64': img_base64,
                'processed_image_path': processed_path,
                'processed_image_url': processed_image_url,
                'server_used': server_addr,
                'message': '处理成功！'
            })

        finally:
            # 减少任务计数
            load_balancer.decrement_task(server_addr)

    except Exception as e: 
        print(f"❌ run workflow error: {e}")
        raise HTTPException(status_code=500, detail=f'Processing error: {e}')


@app.post('/process_video')
async def process_video(request: Request, image: UploadFile = File(...), template: str = Form(...), mode: str = Form('video')):
    """处理视频请求 - 自动负载均衡"""
    if image is None:
        raise HTTPException(status_code=400, detail='No image uploaded')

    # 检查图片文件类型
    if not image.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
        raise HTTPException(status_code=400, detail='Unsupported image format')
        ##

    # 检查模板是否需要重新加载
    if template != tool_pool.current_template:
        if mode == 'video': 
            template_dir = VIDEO_TEMPLATE_DIR
        else: 
            template_dir = IMAGE_TEMPLATE_DIR
        
        template_path = os.path.join(template_dir, template)
        if not os.path. exists(template_path):
            raise HTTPException(status_code=404, detail=f'Template not found in {mode} mode')

        with open(template_path, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
        
        if not workflow:
            raise HTTPException(status_code=500, detail='Failed to load workflow')

        tool_pool.load_workflow(workflow, template)
        print(f"📋 Template {template} loaded for processing")

    # 保存上传的图片（用于视频换脸）
    unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    local_path = os.path.join(UPLOAD_DIR, unique_filename)
    with open(local_path, 'wb') as f:
        shutil.copyfileobj(image.file, f)

    input_path = os.path.join(COMFYUI_INPUT_DIR, unique_filename)
    try:
        shutil.copy(local_path, input_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to copy to input dir: {e}')

    # 获取最空闲的服务器工具
    try: 
        comfy_tool = tool_pool.get_tool_for_request()
        server_addr = comfy_tool. server_address
        
        # 增加任务计数
        load_balancer.increment_task(server_addr)
        
        try:
            if not comfy_tool. workflow:
                raise HTTPException(status_code=500, detail='Workflow not loaded')

            run_result = comfy_tool.run_workflow_with_image(comfy_tool. workflow, unique_filename, timeout=1200)
            
            history_map = run_result.get('history')
            prompt_id = run_result.get('prompt_id')
            
            video_bytes = None
            if isinstance(history_map, dict) and prompt_id in history_map:
                prompt_hist = history_map.get(prompt_id, {})
                outputs = prompt_hist.get('outputs', {})
                for node_output in outputs.values():
                    videos_meta = node_output.get('videos') or node_output. get('gifs')
                    if isinstance(videos_meta, list) and len(videos_meta) > 0 and isinstance(videos_meta[0], dict):
                        first = videos_meta[0]
                        video_bytes = comfy_tool._get_image_bytes(first. get('filename'), first.get('subfolder'), first.get('type'))
                        if video_bytes: 
                            break

            if not video_bytes:
                return JSONResponse(content={
                    'status': 'success',
                    'original_video': local_path,
                    'processed_video_base64': None,
                    'processed_video_path': None,
                    'server_used': server_addr,
                    'message': 'Workflow executed, no video output available'
                })

            processed_filename = f"processed_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
            processed_path = os.path.join(VIDEO_PROCESSED_DIR, processed_filename)
            with open(processed_path, 'wb') as pf:
                pf.write(video_bytes)

            video_base64 = base64.b64encode(video_bytes).decode('utf-8')

            base_url = str(request.base_url).rstrip('/')
            processed_video_url = f"{base_url}/static/{processed_path}"

            return JSONResponse(content={
                'status': 'success',
                'original_video': local_path,
                'processed_video_base64': video_base64,
                'processed_video_path': processed_path,
                'processed_video_url':  processed_video_url,
                'server_used': server_addr,
                'message': '视频处理成功！'
            })

        finally: 
            # 减少任务计数
            load_balancer.decrement_task(server_addr)

    except Exception as e:
        print(f"❌ run video workflow error: {e}")
        raise HTTPException(status_code=500, detail=f'Video processing error: {e}')


if __name__ == '__main__':
    import uvicorn
    # 使用多 worker 模式支持更高并发
    uvicorn.run("backend_improved:app", host='0.0.0.0', port=5000, workers=4)