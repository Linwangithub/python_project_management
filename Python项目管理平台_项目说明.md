# Python 项目管理平台项目说明

Python 项目管理平台是一套面向个人 Python 开发者和 Python 团队的服务器项目管理与自动化运维工具。它的目标是把分散在不同服务器上的 Python 项目、Conda 环境、数据库、Nginx 配置和运行状态集中到一个 Web 页面中统一管理。

## 当前完成度

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

工具开发和缺陷仍在持续修正和补充中，后续会继续围绕易用性、稳定性、部署体验和更多项目类型支持进行增强。

## 项目定位

本项目适用于：

- 个人 Python 开发者管理自己的多台服务器。
- Python 团队统一管理多个项目和部署环境。
- 希望把常见运维操作可视化、自动化的人。
- 需要同时管理项目目录、Conda 环境、数据库和 Nginx 配置的场景。

它不是传统意义上的通用 PaaS 平台，更偏向轻量、可控、贴近真实服务器操作的自动化运维工具。

## 核心能力

1. 项目创建自动化
   - 根据服务器、项目名称、Python 版本、项目目录自动创建项目。
   - 自动创建 Conda 环境。
   - 可选创建 MySQL 数据库。
   - 可选生成 Nginx server block 配置。

2. 项目运行管理
   - 支持前台启动、后台启动和部署启动。
   - 支持安全停止服务，只针对当前项目 PID 操作，避免误杀系统进程。
   - 支持检测服务是否运行，以及实际运行端口。

3. 配置管理
   - 支持项目描述、Conda 环境、入口文件、启动命令、数据库、Nginx 等配置。
   - 设置流程采用工作流方式，便于逐步确认。
   - 对数据库、端口、Nginx 配置做必要校验。

4. 运维可观察性
   - 项目详情展示所有已配置的信息。
   - 日志记录项目创建、设置、删除等关键操作。
   - 右侧终端区域展示自动化执行过程，便于排查问题。

5. 多服务器管理
   - 支持服务器管理和用户分配。
   - 普通用户只看到自己被分配的服务器。
   - root 角色可管理更多资源。

## 技术组成

- 前端：Vue 3、Vite、Pinia、Vue Router、Element Plus。
- 后端：FastAPI、SQLAlchemy、Pydantic、MySQL、Redis、Uvicorn。
- 运维能力：SSH、Conda、MySQL、Nginx。

## 仓库地址

前端：

```text
https://github.com/Linwangithub/python_project_management_frontend
```

后端：

```text
https://github.com/Linwangithub/python_project_management
```

## 开源协议

Apache License 2.0
