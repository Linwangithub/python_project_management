# Python Project Management Backend

Python Project Management Backend 是个人服务器 Python 项目管理平台的后端服务，基于 FastAPI、SQLAlchemy、Pydantic 和 MySQL 构建。

该平台适用于个人 Python 开发者和 Python 团队，用于分布式管理自己的服务器和 Python 项目，属于自动化运维工具。它把项目创建、Conda 环境、数据库、Nginx 配置、项目启动停止、状态检测和操作日志整合到一套后端 API 中。

## 项目状态

基础功能已经完成，可以直接使用：

- 新建项目
- 服务状态
- 检测状态
- 前台启动
- 后台启动
- 部署启动
- 停止服务
- 设置
- 详情
- 日志
- 删除

工具开发和缺陷仍在持续修正和补充中。

## 主要功能

- 用户、角色和权限管理。
- 服务器管理与用户服务器分配。
- 项目目录创建与安全删除。
- Conda 环境创建、检查和删除。
- MySQL 数据库连接检查、创建和删除。
- Nginx 服务检测、配置文件解析、server block 写入和删除。
- 项目前台启动、后台启动、部署启动和安全停止。
- 项目详情、项目操作日志、健康检测和运行端口检测。
- 终端会话、命令执行、自动补全和历史滚动支持。

## 技术栈

- Python 3.12+
- FastAPI
- SQLAlchemy 2.x
- Pydantic 2.x
- MySQL / aiomysql
- Redis，可选
- Uvicorn

## 环境要求

业务服务器需要根据实际功能安装：

- Python / Conda 或 Miniforge
- MySQL，如需数据库创建能力
- Nginx，如需 Nginx 配置管理能力
- SSH 可达，用于远程服务器操作

## 安装依赖

建议使用 Conda 或虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Conda 示例：

```bash
conda create -n project_management python=3.12 -y
conda activate project_management
pip install -r requirements.txt
```

## 配置

复制示例配置：

```bash
cp .env.example .env
```

根据实际环境修改数据库、Redis、JWT 密钥等配置。

可以生成 JWT 密钥：

```bash
python artisan.py init
```

## 数据库迁移

```bash
python artisan.py migrate
```

如果需要清空后重建表结构：

```bash
python artisan.py migrate --refresh
```

## 启动服务

```bash
python main.py -host 0.0.0.0 -port 8888
```

API 文档默认地址：

```text
http://127.0.0.1:8888/api/docs
```

## 前端项目

前端仓库：

```text
https://github.com/Linwangithub/python_project_management_frontend
```

## 安全说明

- 不要提交 `.env`、服务器密码、数据库密码、Token 或私钥。
- 创建服务器时保存的 root 密码属于敏感信息，请仅在可信环境中使用。
- 删除项目、删除 Conda 环境、删除数据库、删除 Nginx 配置属于不可逆操作，请谨慎使用。

## 开源协议

Apache License 2.0
