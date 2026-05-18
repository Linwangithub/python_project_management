"""个人服务器项目管理模块的业务服务层。

当前目录按业务类型拆分：
- project_helpers：项目公共权限、路径、Conda 查询等辅助函数。
- project_checks：项目创建/设置前的名称、数据库、Nginx、端口检测。
- project_create：真实创建项目目录、Conda 环境、数据库和 Nginx 配置。
- project_setting：保存项目设置，并处理 Conda、数据库、Nginx 的实际变更。
- project_runtime：前台启动、后台启动、部署启动、停止服务、复制、导出。
- project_delete：删除项目、Conda、数据库、Nginx 配置。
"""
