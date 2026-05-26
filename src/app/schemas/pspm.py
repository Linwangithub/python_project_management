"""项目管理 Schema 兼容导出模块。

用途：
- 保持历史调用方继续通过 `app.schemas.pspm` 访问全部 PSPM Schema。
- 实际定义已经按用户、环境、服务器、项目、同步、终端等领域拆分到独立模块。
"""

from app.schemas.pspm_admin import *
from app.schemas.pspm_common import *
from app.schemas.pspm_project_check import *
from app.schemas.pspm_project_create import *
from app.schemas.pspm_project_ops import *
from app.schemas.pspm_project_setting import *
from app.schemas.pspm_project_sync import *
from app.schemas.pspm_project_view import *
from app.schemas.pspm_terminal import *
