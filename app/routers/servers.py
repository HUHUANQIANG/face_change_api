"""
Server management API router
"""
from fastapi import APIRouter, Form
from app.models.schemas import ServerStatusResponse, ServerActionResponse

router = APIRouter(prefix="/servers", tags=["servers"])

# Global references will be injected from main.py
load_balancer = None
tool_pool = None


def init_router(lb, tp):
    """Initialize router with load balancer and tool pool instances"""
    global load_balancer, tool_pool
    load_balancer = lb
    tool_pool = tp


@router.get('/status', response_model=ServerStatusResponse)
def get_servers_status():
    """Get status of all ComfyUI servers"""
    return {
        'servers': load_balancer.get_all_status(),
        'total_servers': len(load_balancer.servers)
    }


@router.post('/add', response_model=ServerActionResponse)
def add_server(server_address: str = Form(...)):
    """Dynamically add a new ComfyUI server"""
    tool_pool.add_server(server_address)
    return {'status': 'success', 'message': f'Server {server_address} added'}


@router.post('/remove', response_model=ServerActionResponse)
def remove_server(server_address: str = Form(...)):
    """Dynamically remove a ComfyUI server"""
    load_balancer.remove_server(server_address)
    return {'status': 'success', 'message': f'Server {server_address} removed'}
