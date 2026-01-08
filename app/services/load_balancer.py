"""
ComfyUI Load Balancer Service
"""
import time
import random
import threading
import requests
from typing import List, Dict, Optional
from dataclasses import dataclass

from app.config import settings


@dataclass
class ComfyUIServerStatus:
    """Data class for ComfyUI server status"""
    server_address: str
    is_available: bool = True
    queue_remaining: int = 0
    queue_pending: int = 0
    current_tasks: int = 0  # Current processing tasks
    last_check_time: float = 0
    error_count: int = 0
    
    @property
    def total_load(self) -> int:
        """Calculate total load = queue tasks + current processing tasks"""
        return self.queue_remaining + self.queue_pending + self.current_tasks


class ComfyUILoadBalancer:
    """ComfyUI load balancer with health checking"""
    
    def __init__(self, server_addresses: List[str]):
        self.servers: Dict[str, ComfyUIServerStatus] = {}
        self.lock = threading.RLock()  # Use RLock to avoid deadlocks
        self._running = True
        
        for addr in server_addresses:
            self.servers[addr] = ComfyUIServerStatus(server_address=addr)
        
        # Start background health check thread
        self._start_health_check()
    
    def _start_health_check(self):
        """Start background thread for periodic health checks"""
        def check_loop():
            while self._running:
                for addr in list(self.servers.keys()):
                    self._update_server_status(addr)
                time.sleep(settings.health_check_interval)
        
        thread = threading.Thread(target=check_loop, daemon=True)
        thread.start()
    
    def _update_server_status(self, server_address: str):
        """Update status for a single server"""
        try:
            url = f"http://{server_address}/queue"
            response = requests.get(url, timeout=settings.health_check_timeout)
            
            if response.status_code == 200:
                data = response.json()
                with self.lock:
                    status = self.servers[server_address]
                    # ComfyUI /queue response format:
                    # {"queue_running": [...], "queue_pending": [...]}
                    queue_running = data.get('queue_running', [])
                    queue_pending = data.get('queue_pending', [])
                    
                    status.queue_remaining = len(queue_running)
                    status.queue_pending = len(queue_pending)
                    status.is_available = True
                    status.last_check_time = time.time()
                    # Reset error count on success
                    status.error_count = 0
            else:
                self._mark_server_error(server_address)
        except Exception as e:
            print(f"⚠️ Health check failed for {server_address}: {e}")
            self._mark_server_error(server_address)
    
    def _mark_server_error(self, server_address: str):
        """Mark server as errored"""
        with self.lock:
            status = self.servers[server_address]
            status.error_count += 1
            if status.error_count >= settings.max_error_count:
                status.is_available = False
                print(f"❌ Server {server_address} marked as unavailable")
    
    def get_best_server(self) -> Optional[str]:
        """Get the most idle server address"""
        with self.lock:
            available_servers = [
                (addr, status) for addr, status in self.servers.items()
                if status.is_available
            ]
            if not available_servers:
                return list(self.servers.keys())[0] if self.servers else None
            
            # Shuffle to avoid always selecting the first server when load is equal
            random.shuffle(available_servers)
            available_servers.sort(key=lambda x: x[1].total_load)
            best_server = available_servers[0][0]
            print(f"🎯 Selected server: {best_server} (load: {self.servers[best_server].total_load})")
            return best_server
    
    def increment_task(self, server_address: str):
        """Increment current task count for server"""
        with self.lock:
            if server_address in self.servers:
                self.servers[server_address].current_tasks += 1
    
    def decrement_task(self, server_address: str):
        """Decrement current task count for server"""
        with self.lock:
            if server_address in self.servers:
                self.servers[server_address].current_tasks = max(0, self.servers[server_address].current_tasks - 1)
    
    def get_all_status(self) -> Dict:
        """Get status for all servers"""
        with self.lock:
            return {
                addr: {
                    'is_available': status.is_available,
                    'queue_remaining': status.queue_remaining,
                    'queue_pending': status.queue_pending,
                    'current_tasks': status.current_tasks,
                    'total_load': status.total_load,
                    'error_count': status.error_count
                }
                for addr, status in self.servers.items()
            }
    
    def add_server(self, server_address: str):
        """Dynamically add a new server"""
        with self.lock:
            if server_address not in self.servers:
                self.servers[server_address] = ComfyUIServerStatus(server_address=server_address)
                print(f"➕ Added new server: {server_address}")
    
    def remove_server(self, server_address: str):
        """Dynamically remove a server"""
        with self.lock:
            if server_address in self.servers:
                del self.servers[server_address]
                print(f"➖ Removed server: {server_address}")
    
    def shutdown(self):
        """Gracefully shutdown the load balancer"""
        self._running = False
        print("🛑 Load balancer shutting down...")
