"""
ComfyUI Tool Service
"""
import os
import json
import copy
import time
import uuid
import websocket
import urllib.request
import urllib.parse
import requests
from typing import Optional

from app.config import settings
from app.utils.file_utils import create_placeholder_image


class ComfyUITool:
    """ComfyUI communication wrapper with load balancing support"""
    
    def __init__(self, server_address: str, working_dir: str):
        self.server_address = server_address
        self.working_dir = working_dir
        self.client_id = str(uuid.uuid4())
        self.workflow = None
        self.preloaded = False
    
    def _load_workflow(self, workflow_file: str) -> Optional[dict]:
        """Load workflow from file"""
        try:
            with open(workflow_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to load workflow file: {e}")
            return None
    
    def _queue_prompt(self, workflow: dict) -> dict:
        """Submit prompt to ComfyUI using /prompt endpoint"""
        payload = {"prompt": workflow, "client_id": self.client_id}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"http://{self.server_address}/prompt", data=data)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    
    def _get_history(self, prompt_id: str) -> dict:
        """Get history for a prompt"""
        with urllib.request.urlopen(f"http://{self.server_address}/history/{prompt_id}") as response:
            return json.loads(response.read())
    
    def _get_image_bytes(self, filename: str, subfolder: str, folder_type: str) -> bytes:
        """Get image bytes from ComfyUI"""
        params = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type
        })
        url = f"http://{self.server_address}/view?{params}"
        with urllib.request.urlopen(url) as response:
            return response.read()
    
    def _wait_for_prompt_exec(self, prompt_id: str, timeout: int = 120) -> bool:
        """Open websocket and wait until execution completes"""
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
                            print(f"✅ [{self.server_address}] prompt {prompt_id} executed")
                            return True
            return False
        finally:
            try:
                ws.close()
            except:
                pass
    
    def preload_full_workflow(self, workflow: dict, timeout: int = 300) -> tuple:
        """Preload workflow with placeholder images"""
        try:
            if not workflow:
                return False, 'empty workflow'
            
            placeholder_path = os.path.join(settings.comfyui_input_dir, settings.preload_placeholder_name)
            if not os.path.exists(placeholder_path):
                if not create_placeholder_image(placeholder_path):
                    return False, "failed to create placeholder"
                print(f"✅ Created placeholder at {placeholder_path}")
            
            wf_copy = copy.deepcopy(workflow)
            for nid, node in wf_copy.items():
                if node.get('class_type') == 'LoadImage':
                    inputs = node.setdefault('inputs', {})
                    for k, v in list(inputs.items()):
                        if isinstance(v, str) and (v.endswith('.png') or v.endswith('.jpg') or 'pasted/' in v or 'input' in v):
                            inputs[k] = settings.preload_placeholder_name
                        elif isinstance(v, list):
                            new_list = []
                            changed = False
                            for item in v:
                                if isinstance(item, str) and (item.endswith('.png') or item.endswith('.jpg') or 'pasted/' in item):
                                    new_list.append(settings.preload_placeholder_name)
                                    changed = True
                                else:
                                    new_list.append(item)
                            if changed:
                                inputs[k] = new_list
                    if 'image' not in inputs:
                        inputs['image'] = settings.preload_placeholder_name
            
            print(f"🚀 [{self.server_address}] submitting full workflow for preload (node count={len(wf_copy)})")
            resp = self._queue_prompt(wf_copy)
            prompt_id = resp.get('prompt_id') or resp.get('id') or resp.get('request_id')
            if not prompt_id:
                return False, f"no prompt id returned: {resp}"
            
            ok = self._wait_for_prompt_exec(prompt_id, timeout=timeout)
            if not ok:
                return False, f"preload timeout or ws error, resp={resp}"
            
            self.preloaded = True
            return True, f"preloaded prompt_id={prompt_id}"
        except Exception as e:
            return False, f"exception: {e}"
    
    def run_workflow_with_image(self, workflow: dict, image_filename: str, timeout: int = 300) -> dict:
        """
        Submit workflow replacing LoadImage nodes with provided image_filename.
        Wait for completion and return history outputs.
        """
        wf_copy = copy.deepcopy(workflow)
        target_node_id = "10"
        node = wf_copy.get(target_node_id)
        replaced = False
        
        # Try to replace node with ID 10 first
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
        
        # If ID 10 not found or not LoadImage, find first LoadImage node
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
        
        # Fetch history and return
        history = self._get_history(prompt_id)
        return {'prompt_id': prompt_id, 'history': history}
    
    def run_workflow_with_video(self, workflow: dict, video_filename: str, timeout: int = 600, target_node_id: str = "2") -> dict:
        """Run video workflow"""
        wf_copy = copy.deepcopy(workflow)
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
        
        history = self._get_history(prompt_id)
        return {'prompt_id': prompt_id, 'history': history}
    
    def free_memory(self) -> tuple:
        """Free memory on ComfyUI server"""
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
