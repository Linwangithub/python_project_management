"""应用初始化命令配置模块。

本模块集中维护 artisan.py 迁移初始化时使用的默认角色、权限、用户和命令行提示文案。
将固定数据放在配置模块中，可以避免 CLI 脚本中散落大量硬编码元数据。
"""

# CLI 输出文案：健康检查成功。
CLI_MESSAGE_PONG = 'pong'

# CLI 输出文案：初始化命令开始。
CLI_MESSAGE_INIT = 'init'

# CLI 输出文案：ORM 表结构同步开始。
CLI_MESSAGE_MIGRATE_START = '开始使用 ORM 模型创建/同步表...'

# CLI 输出文案：启用 refresh 时删除所有表。
CLI_MESSAGE_REFRESH_DROP = '检测到 --refresh，正在删除所有表...'

# CLI 输出文案：表删除后重新创建。
CLI_MESSAGE_REFRESH_CREATE = '表删除完成，开始重新创建...'

# CLI 输出文案：表结构同步完成。
CLI_MESSAGE_TABLE_READY = '表结构同步完成。'

# CLI 输出文案：默认用户已存在。
CLI_MESSAGE_ADMIN_EXISTS = '默认用户已存在'

# CLI 输出文案：默认用户创建完成。
CLI_MESSAGE_ADMIN_CREATED = '默认用户已创建：admin / 123456'

# CLI 输出文案：示例命令开始。
CLI_MESSAGE_EXAMPLE_START = '=== 开始数据库和 Redis 连接示例 ==='

# CLI 输出文案：Redis 未配置时跳过。
CLI_MESSAGE_REDIS_SKIPPED = 'Redis 未配置，跳过 Redis 示例。'

# CLI 输出文案：示例命令结束。
CLI_MESSAGE_EXAMPLE_END = '=== 示例结束 ==='

# 生成 API secret key 时使用的随机字节长度。
DEFAULT_SECRET_KEY_BYTES = 32

# 默认管理员账号。
DEFAULT_ADMIN_USER = {
    'userid': 1,
    'username': 'admin',
    'password': '123456',
}

# 默认角色定义：role_key -> 角色元数据。
DEFAULT_ROLE_DEFINITIONS = {
    'root': {
        'role_key': 'root',
        'role_name': 'root用户',
        'description': '超级管理员',
        'status': 1,
    },
    'user': {
        'role_key': 'user',
        'role_name': '普通用户',
        'description': '普通成员',
        'status': 1,
    },
}

# 默认权限定义，字段顺序为 permission_key/menu_key/menu_name/action_key/action_name/description。
DEFAULT_PERMISSION_DEFINITIONS = [
    ('user_management:view', 'user_management', '用户管理', None, None, '菜单可见'),
    ('user_management:create', 'user_management', '用户管理', 'create', '新增', '新增用户'),
    ('user_management:delete', 'user_management', '用户管理', 'delete', '删除', '删除用户'),
    ('user_management:update', 'user_management', '用户管理', 'update', '更新', '更新用户'),
    ('user_management:update_password', 'user_management', '用户管理', 'update_password', '更新密码', '更新用户密码'),
    ('project_management:view', 'project_management', '项目管理', None, None, '菜单可见'),
    ('project_management:create', 'project_management', '项目管理', 'create', '创建', '创建项目'),
    ('project_management:setting', 'project_management', '项目管理', 'setting', '设置', '设置项目端口/命令'),
    ('project_management:start_foreground', 'project_management', '项目管理', 'start_foreground', '前台启动', '前台启动项目'),
    ('project_management:start_background', 'project_management', '项目管理', 'start_background', '后台启动', '后台启动项目'),
    ('project_management:deploy_start', 'project_management', '项目管理', 'deploy_start', '部署启动', '部署方式启动项目'),
    ('project_management:stop', 'project_management', '项目管理', 'stop', '停止服务', '停止项目服务'),
    ('project_management:copy', 'project_management', '项目管理', 'copy', '复制', '复制项目'),
    ('project_management:export', 'project_management', '项目管理', 'export', '导出', '导出项目'),
    ('project_management:delete', 'project_management', '项目管理', 'delete', '删除', '删除项目'),
    ('env_management:view', 'env_management', '环境管理', None, None, '菜单可见'),
    ('env_management:create', 'env_management', '环境管理', 'create', '创建', '创建环境'),
    ('env_management:delete', 'env_management', '环境管理', 'delete', '删除', '删除环境'),
    ('server_management:view', 'server_management', '服务器管理', None, None, '菜单可见'),
    ('server_management:create', 'server_management', '服务器管理', 'create', '创建', '创建服务器'),
    ('server_management:assign', 'server_management', '服务器管理', 'assign', '分配用户', '服务器分配用户'),
    ('server_management:delete', 'server_management', '服务器管理', 'delete', '删除', '删除服务器'),
]

# 普通用户默认允许的权限 key。
DEFAULT_USER_PERMISSION_KEYS = [
    'project_management:view',
    'project_management:create',
    'project_management:setting',
    'project_management:start_foreground',
    'project_management:start_background',
    'project_management:deploy_start',
    'project_management:stop',
    'project_management:copy',
    'project_management:export',
    'project_management:delete',
    'env_management:view',
    'env_management:create',
    'env_management:delete',
]

# example 命令写入 Redis 时使用的演示 ID。
EXAMPLE_DEMO_ID = 1001

# example 命令写入 Redis 的过期时间，单位秒。
EXAMPLE_REDIS_EXPIRE_SECONDS = 3600
