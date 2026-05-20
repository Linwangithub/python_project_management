from fastapi import HTTPException
from sqlalchemy import text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from app.utils.pspm.project_config import SAFE_IDENTIFIER_RE


def _safe_optional_db_name(name: str) -> str:
  """校验可选数据库名。

  参数：
  - name：数据库名，可以为空。

  作用：
  - 创建项目时数据库配置是可选项。
  - 设置项目时如果用户清空数据库名，则表示不启用数据库配置。

  返回：
  - 空输入返回空字符串；非空时返回 `_safe_db_identifier` 校验后的数据库名。
  """
  value = (name or '').strip()
  if not value:
    return ''
  return _safe_db_identifier(value)


def _safe_db_identifier(name: str) -> str:
  """校验数据库标识符。

  参数：
  - name：数据库名。

  作用：
  - 数据库名会被拼进 CREATE/DROP DATABASE SQL，因此只能允许字母、数字、下划线。

  返回：
  - 合法数据库名。
  """
  value = (name or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='数据库名不能为空')
  if not SAFE_IDENTIFIER_RE.match(value):
    raise HTTPException(status_code=400, detail=f'数据库名不合法：{value}')
  return value


def _safe_db_host(host: str) -> str:
  """校验数据库 IP/Host。

  参数：
  - host：数据库连接地址。

  作用：
  - 防止空地址和包含空格的明显非法地址进入连接 URL。

  返回：
  - 去掉首尾空白后的地址。
  """
  value = (host or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='数据库IP不能为空')
  if ' ' in value:
    raise HTTPException(status_code=400, detail='数据库IP格式不合法')
  return value


def _safe_db_port(port: int | None) -> int:
  """校验数据库端口。

  参数：
  - port：数据库端口，可以是 int 或 None。

  作用：
  - MySQL 连接测试、创建数据库、删除数据库前统一校验端口。

  返回：
  - 合法端口数字。
  """
  if port is None:
    raise HTTPException(status_code=400, detail='数据库端口不能为空')
  if port <= 0 or port > 65535:
    raise HTTPException(status_code=400, detail='数据库端口范围不合法')
  return int(port)


def _safe_db_user(username: str) -> str:
  """校验数据库账号。

  参数：
  - username：数据库账号。

  作用：
  - 数据库连接测试、创建数据库、删除数据库前统一校验账号非空。

  返回：
  - 去掉首尾空白后的账号。
  """
  value = (username or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='数据库账号不能为空')
  return value


def _build_db_url(host: str, port: int, username: str, password: str, database: str) -> URL:
  """构建 SQLAlchemy 异步 MySQL URL。

  参数：
  - host/port/username/password：数据库连接信息。
  - database：要连接的数据库名，通常是 `mysql` 系统库或目标业务库。

  返回：
  - SQLAlchemy `URL` 对象，供 `create_async_engine` 使用。
  """
  return URL.create(
    drivername='mysql+aiomysql',
    username=username,
    password=password,
    host=host,
    port=port,
    database=database,
  )


async def _check_server_mysql_connectable(host: str, port: int, username: str, password: str) -> tuple[bool, str]:
  """检查 MySQL 是否可连接。

  参数：
  - host/port/username/password：数据库连接信息，来自前端数据库配置。

  作用：
  - 创建项目或设置数据库前，只验证 MySQL 服务和账号密码是否可用。
  - 这里连接 `mysql` 系统库，不要求目标业务库已存在。

  返回：
  - `(True, '连接成功')`：连接成功。
  - `(False, '连接失败：...')`：连接失败，调用方可选择是否隐藏失败原因。
  """
  engine = create_async_engine(
    _build_db_url(host, port, username, password, 'mysql'),
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'connect_timeout': 5},
  )
  try:
    async with engine.connect() as conn:
      await conn.execute(sa_text('SELECT 1'))
    return True, '连接成功'
  except Exception as ex:
    return False, f'连接失败：{str(ex)}'
  finally:
    await engine.dispose()


async def _check_database_exists(host: str, port: int, username: str, password: str, db_name: str) -> bool:
  """检查目标数据库是否存在。

  参数：
  - host/port/username/password：数据库连接信息。
  - db_name：目标数据库名。

  作用：
  - 创建项目前防止创建同名数据库。
  - 设置数据库时防止新数据库名冲突。

  返回：
  - True：数据库已存在。
  - False：数据库不存在。
  """
  engine = create_async_engine(
    _build_db_url(host, port, username, password, 'mysql'),
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'connect_timeout': 5},
  )
  try:
    async with engine.connect() as conn:
      result = await conn.execute(
        sa_text('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :name LIMIT 1'),
        {'name': db_name},
      )
      row = result.first()
      return row is not None
  finally:
    await engine.dispose()


async def _list_database_names(host: str, port: int, username: str, password: str) -> list[str]:
  """查询当前账号可见的业务数据库名称列表。

  参数：
  - host/port/username/password：数据库连接信息。

  作用：
  - 同步已有项目时，先测试 MySQL 连接。
  - 连接通过后返回数据库下拉框选项，用户只能从已存在数据库中选择。

  返回：
  - 数据库名称列表，已过滤 MySQL 系统库。
  """
  system_databases = {'information_schema', 'mysql', 'performance_schema', 'sys'}
  engine = create_async_engine(
    _build_db_url(host, port, username, password, 'mysql'),
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'connect_timeout': 5},
  )
  try:
    async with engine.connect() as conn:
      result = await conn.execute(sa_text('SHOW DATABASES'))
      names = []
      for row in result:
        name = str(row[0] or '').strip()
        if name and name not in system_databases:
          names.append(name)
      return sorted(names)
  finally:
    await engine.dispose()


async def _create_database_utf8mb4(host: str, port: int, username: str, password: str, db_name: str) -> None:
  """创建 utf8mb4 编码数据库。

  参数：
  - host/port/username/password：数据库连接信息。
  - db_name：要创建的数据库名，调用前必须已通过 `_safe_db_identifier` 校验。

  作用：
  - 新建项目启用数据库时真实创建业务数据库。

  返回：
  - 无返回值；创建失败会抛出 SQLAlchemy 异常给调用方处理。
  """
  engine = create_async_engine(
    _build_db_url(host, port, username, password, 'mysql'),
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'connect_timeout': 10},
  )
  ddl = f'CREATE DATABASE `{db_name}` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */'
  try:
    async with engine.begin() as conn:
      await conn.execute(sa_text(ddl))
  finally:
    await engine.dispose()


async def _drop_database_if_exists(host: str, port: int, username: str, password: str, db_name: str) -> None:
  """删除目标数据库。

  参数：
  - host/port/username/password：数据库连接信息。
  - db_name：要删除的数据库名，调用前必须已通过 `_safe_db_identifier` 校验。

  作用：
  - 创建项目失败回滚时删除刚创建的数据库。
  - 设置数据库时按用户选择删除原数据库。
  - 删除项目时按删除范围删除项目数据库。

  返回：
  - 无返回值；删除失败会抛出 SQLAlchemy 异常给调用方处理。
  """
  engine = create_async_engine(
    _build_db_url(host, port, username, password, 'mysql'),
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={'connect_timeout': 10},
  )
  ddl = f'DROP DATABASE IF EXISTS `{db_name}`'
  try:
    async with engine.begin() as conn:
      await conn.execute(sa_text(ddl))
  finally:
    await engine.dispose()
