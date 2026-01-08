"""
ComfyUI Tool Pool Service
"""
import os
import threading
from typing import Dict
from concurrent.futures import ThreadPoolExecutor

from app.services.load_balancer import ComfyUILoadBalancer
from app.services.comfyui_tool import ComfyUITool
from app.config import settings


class ComfyUIToolPool:
    """ComfyUI tool pool manager"""
    
    def __init__(self, load_balancer: ComfyUILoadBalancer):
        self.load_balancer = load_balancer
        self.tools: Dict[str, ComfyUITool] = {}
        self.lock = threading.RLock()  # Use RLock to avoid deadlocks
        self.workflow = None
        self.current_template = None
        
        # Create tool instance for each server
        for server_addr in load_balancer.servers.keys():
            self.tools[server_addr] = ComfyUITool(server_addr, working_dir=os.getcwd())
    
    def get_tool_for_request(self) -> ComfyUITool:
        """Get the best tool instance for current request"""
        best_server = self.load_balancer.get_best_server()
        if not best_server:
            raise RuntimeError("No available ComfyUI servers")
        
        with self.lock:
            if best_server not in self.tools:
                self.tools[best_server] = ComfyUITool(best_server, working_dir=os.getcwd())
            
            tool = self.tools[best_server]
            # Sync workflow to this tool
            if self.workflow:
                tool.workflow = self.workflow
            
            return tool
    
    def load_workflow(self, workflow: dict, template_name: str):
        """Load workflow to all tools with thread safety"""
        with self.lock:
            self.workflow = workflow
            self.current_template = template_name
            for tool in self.tools.values():
                tool.workflow = workflow
    
    def preload_all_servers(self, workflow: dict, timeout: int = 300) -> Dict[str, tuple]:
        """Preload workflow on all servers in parallel"""
        results = {}
        
        def preload_on_server(server_addr: str):
            tool = self.tools.get(server_addr)
            if tool:
                return server_addr, tool.preload_full_workflow(workflow, timeout)
            return server_addr, (False, "No tool instance")
        
        # Use thread pool for parallel preloading
        with ThreadPoolExecutor(max_workers=len(self.tools)) as executor:
            futures = [executor.submit(preload_on_server, addr) for addr in self.tools.keys()]
            for future in futures:
                addr, result = future.result()
                results[addr] = result
        
        return results
    
    def add_server(self, server_address: str):
        """Dynamically add new server"""
        self.load_balancer.add_server(server_address)
        with self.lock:
            if server_address not in self.tools:
                tool = ComfyUITool(server_address, working_dir=os.getcwd())
                if self.workflow:
                    tool.workflow = self.workflow
                self.tools[server_address] = tool
