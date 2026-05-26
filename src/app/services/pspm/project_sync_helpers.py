"""同步已有项目的基础校验和路径工具。

本模块集中维护同步项目流程中不依赖数据库事务的纯校验逻辑，包括可浏览根目录、
路径越界保护、入口文件路径校验、Python 版本输出清洗和已存在数据库名校验。
"""

from __future__ import annotations

import os

from fastapi import HTTPException

from app.utils.pspm.path_utils import _normalize_path, _safe_rel_path_input
from app.utils.pspm.project_config import TERMINAL_HOME_DIR, USER_HOME_BASE_PATH_TEMPLATE
from app.utils.pspm.shell_utils import _split_lines


def _project_base_path_for_user(current_user, is_root: bool) -> str:
  """返回当前用户同步已有项目时允许浏览的项目根目录。

  参数：
  - current_user：当前登录用户。
  - is_root：当前用户是否为 root 角色。

  作用：
  - 同步已有项目只能在配置文件定义的项目目录前缀下选择目录。
  - root 使用 root 起始目录，普通用户使用配置中的 home 目录模板。

  返回：
  - 绝对路径字符串。
  """
  username = str(getattr(current_user, 'username', '') or 'user').strip() or 'user'
  if is_root:
    return TERMINAL_HOME_DIR
  return _normalize_path(USER_HOME_BASE_PATH_TEMPLATE.replace('{username}', username))

def _clean_python_version_output(text: str) -> str:
  """清洗 Conda Python 版本输出。

  参数：
  - text：`conda run -n xxx python --version` 的 stdout/stderr 合并文本。

  作用：
  - SSH 首次连接远程服务器时，stderr 里可能包含 `Warning: Permanently added ...`。
  - 前端只需要展示实际 Python 版本，不应该把 SSH warning 混进去。

  返回：
  - 形如 `Python 3.8.13` 的版本文本；未匹配到时返回原始非 warning 文本。
  """
  lines = [x.strip() for x in _split_lines(text) if x.strip()]
  for line in lines:
    if line.lower().startswith('python '):
      return line
  filtered = [line for line in lines if not line.lower().startswith('warning: permanently added')]
  return ' '.join(filtered).strip()

def _safe_sync_abs_path(base_path: str, rel_path: str) -> str:
  """把同步弹框传入的相对目录解析为安全绝对路径。

  参数：
  - base_path：允许的项目根目录。
  - rel_path：前端级联目录相对路径。

  作用：
  - 防止通过 `..` 或绝对路径越过项目根目录。

  返回：
  - 解析后的绝对路径。
  """
  base = _normalize_path(base_path)
  rel = _safe_rel_path_input(rel_path)
  if not rel:
    return base
  target = os.path.normpath(os.path.join(base, rel))
  if target != base and not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail='项目目录越界')
  return target

def _safe_sync_backend_path(base_path: str, backend_path: str) -> str:
  """校验同步项目目录必须存在于允许前缀下。"""
  base = _normalize_path(base_path)
  target = _normalize_path(backend_path)
  if target == base:
    raise HTTPException(status_code=400, detail='请选择具体项目目录，不能选择项目根目录')
  if not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail=f'项目目录必须位于 {base} 下')
  return target

def _safe_sync_entry_file_path(backend_path: str, entry_file_path: str) -> str:
  """校验同步已有项目时选择的入口文件必须位于项目目录内。

  参数：
  - backend_path：已经选择的项目目录绝对路径。
  - entry_file_path：前端提交的入口文件绝对路径，可以为空。

  作用：
  - 同步已有项目时允许一起登记入口文件。
  - 如果用户选择了入口文件，则必须保证它没有越过项目目录。

  返回：
  - 空字符串，或标准化后的入口文件绝对路径。
  """
  value = str(entry_file_path or '').strip()
  if not value:
    return ''
  base = _normalize_path(backend_path)
  target = _normalize_path(value)
  if target == base or not target.startswith(f'{base}/'):
    raise HTTPException(status_code=400, detail=f'入口文件必须位于项目目录 {base} 下')
  return target

def _safe_existing_database_name_from_list(database_name: str, visible_databases: list[str]) -> str:
  """校验同步已有项目选择的数据库名必须来自服务器实际数据库列表。

  参数：
  - database_name：前端下拉框选中的数据库名。
  - visible_databases：当前账号通过 `SHOW DATABASES` 查询到的业务数据库列表。

  作用：
  - 同步已有项目不是创建新数据库，不能复用只允许字母数字下划线的创建数据库校验。
  - 只允许选择真实存在且当前账号可见的数据库名，避免前端手工篡改提交值。

  返回：
  - 原始数据库名，保留短横线、点号、中文等 MySQL 已存在库名字符。
  """
  value = str(database_name or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='数据库名不能为空')
  for item in visible_databases or []:
    candidate = str(item or '').strip()
    if candidate == value:
      return candidate
  raise HTTPException(status_code=400, detail=f'数据库不存在或当前账号不可见：{value}')
