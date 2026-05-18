from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from asgi_correlation_id.context import correlation_id
import logging

logger = logging.getLogger(__name__)


def register(app: FastAPI) -> None:
    # 注册未处理异常处理器
    app.add_exception_handler(Exception, global_exception_handler)
    # 注册HTTP异常处理器
    app.add_exception_handler(StarletteHTTPException, custom_http_exception_handler)
    # 注册请求验证错误处理器
    app.add_exception_handler(RequestValidationError, validation_exception_handler)


async def global_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """全局异常处理程序，捕获所有未处理的异常"""
    # 记录详细的错误日志，包括堆栈跟踪
    logger.error(f"未捕获的异常: {str(exc)}", exc_info=True)

    # 构建统一的错误响应
    custom_response = {
        "message": "Internal Server Error",
        "status": "error",
        "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "timestamp": int(
            request.state.start_time.timestamp() * 1000
        ) if hasattr(request.state, 'start_time') else int(
            __import__('time').time() * 1000
        )
    }

    # 添加请求ID
    headers = {}
    headers.setdefault("X-Request-ID", correlation_id.get() or "")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=custom_response,
        headers=headers
    )


async def custom_http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """自定义HTTP异常处理程序"""
    # 记录错误信息（非500错误通常不需要堆栈跟踪）
    if exc.status_code >= 500:
        logger.error(f"HTTP异常: {exc.status_code} - {exc.detail}", exc_info=True)
    else:
        logger.warning(f"HTTP异常: {exc.status_code} - {exc.detail}")

    custom_response = {
        "message": exc.detail,
        "status": "error",
        "code": exc.status_code,
        "timestamp": int(
            request.state.start_time.timestamp() * 1000
        ) if hasattr(request.state, 'start_time') else int(
            __import__('time').time() * 1000
        )
    }

    # 添加相关ID头信息
    headers = getattr(exc, "headers", {}) or {}
    headers.setdefault("X-Request-ID", correlation_id.get() or "")

    return JSONResponse(
        status_code=exc.status_code,
        content=custom_response,
        headers=headers
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """请求验证错误处理程序"""
    # 记录验证错误详情
    logger.warning(f"请求验证错误: {exc.errors()}")

    # 格式化验证错误信息
    error_details = []
    for error in exc.errors():
        field = '.'.join(str(loc) for loc in error['loc'])
        message = error['msg']
        error_details.append(f"{field}: {message}")

    custom_response = {
        "message": "请求参数验证失败",
        "status": "error",
        "code": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "data": error_details,
        "timestamp": int(
            request.state.start_time.timestamp() * 1000
        ) if hasattr(request.state, 'start_time') else int(
            __import__('time').time() * 1000
        )
    }

    headers = {}
    headers.setdefault("X-Request-ID", correlation_id.get() or "")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=custom_response,
        headers=headers
    )
