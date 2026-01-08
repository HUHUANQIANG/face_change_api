# 重构说明 (Refactoring Guide)

## 概述

本次重构将 `backend_improved.py` (818行) 拆分为模块化结构，提高了代码的可维护性、可测试性和并发安全性。

## 新的目录结构

```
face_change_api/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI 应用入口
│   ├── config.py                    # 配置管理（使用 pydantic-settings）
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── servers.py               # 服务器管理路由 (/servers/*)
│   │   ├── templates.py             # 模板管理路由 (/templates, /load_template)
│   │   ├── images.py                # 图片处理路由 (/process_image)
│   │   └── videos.py                # 视频处理路由 (/process_video)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── load_balancer.py         # ComfyUILoadBalancer 和 ComfyUIServerStatus
│   │   ├── comfyui_tool.py          # ComfyUITool 类
│   │   └── tool_pool.py             # ComfyUIToolPool 类
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py               # Pydantic 响应模型
│   └── utils/
│       ├── __init__.py
│       └── file_utils.py            # 文件操作工具函数
├── run.py                           # 启动脚本
└── requirements.txt                 # 依赖列表
```

## 主要改进

### 1. 代码模块化
- **问题**: 所有代码混在单个文件中，难以维护和测试
- **解决**: 按功能拆分为多个模块，每个模块职责清晰

### 2. 并发安全增强
- **问题**: 模板加载存在竞态条件，未使用的 ThreadPoolExecutor
- **解决**:
  - 使用 `threading.RLock()` 替代 `threading.Lock()` 避免死锁
  - 为模板加载添加锁保护 (在 `tool_pool.load_workflow()`)
  - 添加服务器自动恢复机制（错误计数在成功时重置）
  - 添加负载均衡器的优雅关闭方法 `shutdown()`

### 3. 配置管理改进
- **问题**: 配置硬编码在代码中
- **解决**: 使用 pydantic-settings 管理配置
  - 支持从环境变量读取
  - 支持从 `.env` 文件读取
  - 所有配置项集中管理

### 4. API 文档改进
- **问题**: 缺少规范的响应模型
- **解决**: 添加 Pydantic 响应模型，提高 API 文档质量和类型安全

### 5. API 兼容性
所有原有 API 端点保持兼容：
- GET `/servers/status` - 获取服务器状态
- POST `/servers/add` - 添加服务器
- POST `/servers/remove` - 移除服务器
- GET `/templates` - 获取模板列表
- POST `/load_template` - 加载模板
- POST `/process_image` - 处理图片
- POST `/process_video` - 处理视频

## 使用方法

### 安装依赖
```bash
pip install -r requirements.txt
```

### 配置环境变量（可选）
创建 `.env` 文件：
```env
# ComfyUI 服务器地址（逗号分隔）
COMFYUI_SERVERS=["127.0.0.1:8155", "127.0.0.1:8166"]

# 目录配置
COMFYUI_INPUT_DIR=/path/to/comfyui/input/
UPLOAD_DIR=uploaded_images
PROCESSED_DIR=processed_images

# 服务器配置
HOST=0.0.0.0
PORT=5000
WORKERS=4

# 超时配置
WORKFLOW_TIMEOUT=600
VIDEO_WORKFLOW_TIMEOUT=1200
```

### 启动应用

**方式一：使用启动脚本**
```bash
python run.py
```

**方式二：直接使用 uvicorn**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 4
```

**方式三：开发模式（自动重载）**
```bash
uvicorn app.main:app --reload
```

## 迁移指南

### 从 backend_improved.py 迁移

1. **更新导入**: 如果有其他代码导入 `backend_improved.py`，需要更新为：
   ```python
   # 旧的
   from backend_improved import app
   
   # 新的
   from app.main import app
   ```

2. **配置调整**: 将硬编码的配置移至环境变量或 `.env` 文件

3. **启动命令**: 
   ```bash
   # 旧的
   python backend_improved.py
   
   # 新的
   python run.py
   # 或
   uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 4
   ```

## 测试

启动服务器后，可以通过以下方式测试：

### 1. 查看 API 文档
访问 http://localhost:5000/docs

### 2. 测试基本功能
```bash
# 测试根路径
curl http://localhost:5000/

# 查看服务器状态
curl http://localhost:5000/servers/status

# 查看模板列表
curl http://localhost:5000/templates?mode=image

# 添加服务器
curl -X POST http://localhost:5000/servers/add -F "server_address=127.0.0.1:8188"
```

## 注意事项

1. 确保 ComfyUI 服务器正常运行
2. 确保工作流模板文件存在于 `workflows/image/` 和 `workflows/video/` 目录
3. 多 worker 模式下，每个 worker 会独立维护负载均衡器状态

## 技术栈

- **FastAPI**: Web 框架
- **Pydantic**: 数据验证和设置管理
- **pydantic-settings**: 配置管理
- **Uvicorn**: ASGI 服务器
- **threading**: 并发控制
- **websocket-client**: WebSocket 通信
- **Pillow**: 图片处理
