from typing import Any
import re
import json
from pydantic import model_validator


class Helpers:

    def pascal_case_to_snake_case(self, camel_case: str) -> str:
        """大驼峰（帕斯卡）转蛇形"""
        snake_case = re.sub(r"(?P<key>[A-Z])", r"_\g<key>", camel_case)
        return snake_case.lower().strip('_')

    def snake_case_to_pascal_case(self, snake_case: str) -> str:
        """蛇形转大驼峰（帕斯卡）"""
        words = snake_case.split('_')
        return ''.join(word.title() for word in words)

    def string_to_json(self, s: str) -> tuple[bool, Any]:
        """将字符串转换为 JSON 对象（仅对象/数组），否则原样返回"""
        try:
            obj = json.loads(s)
            if isinstance(obj, (dict, list)):
                return True, obj
            else:
                return False, s
        except (ValueError, TypeError):
            return False, s

    def json_to_string(self, obj: Any) -> tuple[bool, str]:
        """将 JSON 对象转换为字符串"""
        try:
            if isinstance(obj, (dict, list)):
                s = json.dumps(obj, ensure_ascii=False)
                return True, s
            else:
                return False, str(obj)
        except (TypeError, ValueError):
            return False, str(obj)

    @model_validator(mode="after")
    def check(self) -> "Helpers":
        """Check everything correct."""
        return self
