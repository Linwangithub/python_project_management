# FastAPI Template

这是一个基础 FastAPI 后端模板，保留以下能力：

- FastAPI 应用启动与 `lifespan` 生命周期管理
- `.env` / 环境变量读取配置
- JWT 登录认证与当前用户依赖
- 个人用户接口与 RBAC 用户 CRUD 示例
- SQLAlchemy 2.x async ORM 模式
- 异步 MySQL 连接：`mysql+aiomysql`
- 异步 Redis 连接
- 统一响应 schema
- 通用 CRUD 基类
- WebSocket ping/pong 示例
- APScheduler 系统定时任务示例
- Typer 命令入口：`artisan.py`

## 常用命令

```bash
uv sync
uv run artisan.py init
uv run artisan.py migrate
uv run main.py
```

默认管理员账号由 `migrate` 命令创建：

- 用户名：`admin`
- 密码：`admin@123456`
