"""项目设置字段归一化和日志动作工具。

本模块集中维护设置弹框提交值的清洗、入口文件路径校验、字段差异判断，
以及日志 actions 文案构造。实际 Conda、数据库、Nginx 变更仍由 project_setting.py 负责。
"""

from __future__ import annotations

import os

from fastapi import HTTPException

from app import schemas
from app.utils.pspm.db_utils import _safe_db_host, _safe_db_port, _safe_db_user, _safe_optional_db_name
from app.utils.pspm.path_utils import _normalize_path, _safe_conda_name, _safe_entry_file_path, _safe_optional_port_text, _safe_python_version


SETTING_ACTION_FIELD_LABELS = {
  'description': '项目描述',
  'conda_env_name': 'Conda环境',
  'python_version': 'Python版本',
  'entry_file_path': '项目入口文件位置',
  'backend_dev_port': '后端开发端口',
  'backend_deploy_port': '后端部署端口',
  'frontend_port': 'Nginx前端端口',
  'dev_start_command': '开发启动命令',
  'deploy_start_command': '部署启动命令',
  'database_name': '数据库名称',
  'database_host': '数据库IP',
  'database_port': '数据库端口',
  'database_user': '数据库账号',
  'database_password': '数据库密码',
  'nginx_server_ip': 'Nginx服务器IP',
  'nginx_conf_path': 'Nginx配置文件路径',
  'nginx_config_text': 'Nginx详细配置',
  'frontend_path': '前端打包文件位置',
}


def _text(value) -> str:
  """把配置值统一转换成去掉首尾空白的字符串。"""
  return str(value or '').strip()

def _same_text(left, right) -> bool:
  """判断两个配置值是否一致；None 和空字符串都视为空配置。"""
  return _text(left) == _text(right)

def _changed_fields_for_update(project, data_in: dict) -> dict:
  """只保留和项目原配置不一致的字段，避免后端重复更新。"""
  changed: dict = {}
  for key, value in data_in.items():
    if key == 'nginx_enabled':
      continue
    if not hasattr(project, key):
      continue
    if _same_text(getattr(project, key, ''), value):
      continue
    changed[key] = value
  return changed

def _format_action_value(value) -> str:
  """把配置变更值转换成适合写入 actions 的短文本。"""
  text = _text(value)
  if not text:
    return '空'
  if len(text) > 180:
    return f'{text[:180]}...'
  return text

def build_setting_actions_from_changed_fields(changed_fields: list[dict]) -> list[str]:
  """把普通字段差异补充到日志 actions，方便日志弹框直接展示完整动作。"""
  actions: list[str] = []
  for item in changed_fields or []:
    key = item.get('key')
    if key in {'id', 'owner_id', 'server_id', 'status', 'auto_start', 'created_at', 'updated_at'}:
      continue
    label = SETTING_ACTION_FIELD_LABELS.get(key, item.get('label') or key)
    before = _format_action_value(item.get('before'))
    after = _format_action_value(item.get('after'))
    actions.append(f'修改{label}：{before} -> {after}')
  return actions

def _safe_setting_entry_file_path(project, entry_file_path: str) -> str:
  """校验设置弹框提交的入口文件路径。

  参数：
  - project：项目 ORM 对象，用于读取项目根目录 `backend_path`。
  - entry_file_path：前端提交的入口文件路径，可能是相对路径或绝对路径。

  作用：
  - 新建项目后设置入口文件时，前端通常提交相对路径。
  - 同步已有项目时，数据库中可能已经保存绝对路径。
  - 绝对路径必须位于当前项目目录内，防止越界保存。

  返回：
  - 标准化后的入口文件路径；相对输入返回相对路径，绝对输入返回绝对路径。
  """
  value = _text(entry_file_path)
  if not value:
    raise HTTPException(status_code=400, detail='项目入口文件位置不能为空')
  if os.path.isabs(value):
    backend_path = _normalize_path(getattr(project, 'backend_path', '') or '')
    target = os.path.normpath(value)
    if target == backend_path or not target.startswith(f'{backend_path}/'):
      raise HTTPException(status_code=400, detail='项目入口文件位置超出项目目录')
    return target
  return _safe_entry_file_path(value)

def normalize_project_setting_payload(payload: schemas.pspm.ProjectSettingUpdate, project) -> tuple[dict, bool, bool, bool, bool]:
  """整理项目设置请求体，生成可写入项目表的字段字典。"""
  drop_original_database = bool(getattr(payload, 'drop_original_database', False))
  create_conda_env = bool(getattr(payload, 'create_conda_env', False))
  drop_original_conda_env = bool(getattr(payload, 'drop_original_conda_env', False))
  drop_original_nginx_config = bool(getattr(payload, 'drop_original_nginx_config', False))

  data_in = payload.model_dump(exclude_none=True)
  data_in.pop('drop_original_database', None)
  data_in.pop('create_conda_env', None)
  data_in.pop('drop_original_conda_env', None)
  data_in.pop('drop_original_nginx_config', None)

  if 'description' in data_in:
    data_in['description'] = _text(data_in.get('description'))
  if 'conda_env_name' in data_in:
    conda_env_value = _text(data_in.get('conda_env_name'))
    data_in['conda_env_name'] = _safe_conda_name(conda_env_value) if conda_env_value else ''
  if 'python_version' in data_in:
    python_version_value = _text(data_in.get('python_version'))
    data_in['python_version'] = _safe_python_version(python_version_value) if python_version_value else ''
  if 'entry_file_path' in data_in:
    data_in['entry_file_path'] = _safe_setting_entry_file_path(project, data_in.get('entry_file_path') or '')
  if 'backend_dev_port' in data_in:
    data_in['backend_dev_port'] = _safe_optional_port_text(data_in.get('backend_dev_port'))
  if 'backend_deploy_port' in data_in:
    data_in['backend_deploy_port'] = _safe_optional_port_text(data_in.get('backend_deploy_port'))
  if 'frontend_port' in data_in:
    data_in['frontend_port'] = _safe_optional_port_text(data_in.get('frontend_port'))

  database_keys = {'database_name', 'database_host', 'database_port', 'database_user', 'database_password'}
  if database_keys.intersection(data_in.keys()):
    db_name_value = _safe_optional_db_name(_text(data_in.get('database_name'))) if 'database_name' in data_in else _safe_optional_db_name(_text(getattr(project, 'database_name', '')))
    if not db_name_value:
      data_in['database_name'] = ''
      data_in['database_host'] = ''
      data_in['database_port'] = ''
      data_in['database_user'] = ''
      data_in['database_password'] = ''
    else:
      data_in['database_name'] = db_name_value
      if 'database_host' in data_in:
        host_value = _text(data_in.get('database_host'))
        data_in['database_host'] = _safe_db_host(host_value) if host_value else ''
      if 'database_port' in data_in:
        port_value = _text(data_in.get('database_port'))
        data_in['database_port'] = str(_safe_db_port(int(port_value))) if port_value else ''
      if 'database_user' in data_in:
        user_value = _text(data_in.get('database_user'))
        data_in['database_user'] = _safe_db_user(user_value) if user_value else ''
      if 'database_password' in data_in:
        data_in['database_password'] = str(data_in.get('database_password') or '')

  return data_in, drop_original_database, create_conda_env, drop_original_conda_env, drop_original_nginx_config
