"""项目接口响应文案配置。

本模块集中维护项目接口层使用的固定返回文案，避免路由函数中散落硬编码字符串。
"""

# 同步已有项目成功提示。
MSG_PROJECT_SYNC_SUCCESS = '同步成功'

# 新建项目成功提示。
MSG_PROJECT_CREATE_SUCCESS = '创建成功'

# 保存项目设置成功提示。
MSG_PROJECT_SETTING_SUCCESS = '设置保存成功'

# 删除原数据库成功提示。
MSG_PROJECT_ORIGINAL_DATABASE_DELETED = '原数据库已删除'

# 前台启动参数准备完成提示。
MSG_PROJECT_FOREGROUND_PREPARED = '前台启动参数准备完成'

# 前台启动成功兜底提示。
MSG_PROJECT_FOREGROUND_STARTED = '前台启动成功'

# 后台启动成功兜底提示。
MSG_PROJECT_BACKGROUND_STARTED = '后台启动成功'

# 部署启动成功兜底提示。
MSG_PROJECT_DEPLOY_STARTED = '部署启动成功'

# 停止服务成功兜底提示。
MSG_PROJECT_STOPPED = '停止服务成功'
