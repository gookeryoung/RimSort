"""性能插桩工具。

提供轻量级阶段计时与函数级耗时记录,用于建立性能基线与定位热点。
默认通过环境变量 ``RIMSORT_PERF=1`` 启用,未启用时所有调用均为空操作,
确保生产环境零开销。

用法示例::

    from app.utils.perf_timing import log_stage

    with log_stage("initialize_settings"):
        self.initialize_settings()

    @timed("sort_paths")
    def sort_paths(...): ...

设计要点:
- 未启用时 ``log_stage`` 返回 ``nullcontext``、``timed`` 返回原函数,零开销
- 启用时通过 loguru INFO 输出,便于在现有日志中筛选
- 计时用 ``time.perf_counter``(纳秒级分辨率,不受 NTP 影响)
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from functools import wraps
from typing import TypeVar

from loguru import logger

_T = TypeVar("_T")


def is_perf_enabled() -> bool:
    """返回性能插桩是否启用。"""
    return os.environ.get("RIMSORT_PERF", "") == "1"


class _StageTimer(AbstractContextManager[None]):
    """阶段计时上下文管理器。

    进入时记录起始时间,退出时通过 loguru 输出耗时。仅当
    ``RIMSORT_PERF=1`` 时由 ``log_stage`` 构造,否则 ``log_stage``
    返回 ``nullcontext``。

    :param name: 阶段名称,用于日志标识
    """

    __slots__ = ("_name", "_start")

    def __init__(self, name: str) -> None:
        self._name = name
        self._start: float = 0.0

    def __enter__(self) -> None:
        self._start = time.perf_counter()

    def __exit__(self, *exc_info: object) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        logger.info(f"[perf] {self._name}: {elapsed_ms:.2f}ms")


def log_stage(name: str) -> AbstractContextManager[None]:
    """记录代码块耗时。

    :param name: 阶段名称
    :return: 启用时返回 ``_StageTimer``,未启用返回 ``nullcontext``
    """
    if not is_perf_enabled():
        return nullcontext()
    return _StageTimer(name)


def timed(name: str) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """函数级耗时装饰器。

    :param name: 函数标识名称
    :return: 装饰器,未启用时返回原函数不变

    用法::

        @timed("refresh_metadata")
        def refresh_metadata(self): ...
    """

    def decorator(func: Callable[..., _T]) -> Callable[..., _T]:
        if not is_perf_enabled():
            return func

        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> _T:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                logger.info(f"[perf] {name}: {elapsed_ms:.2f}ms")

        return wrapper

    return decorator


def perf_now() -> float:
    """返回 ``time.perf_counter`` 当前值,用于自定义计时场景。

    未启用时仍返回 ``perf_counter`` 值(开销极小),由调用方决定是否记录。
    """
    return time.perf_counter()


def perf_log(name: str, start: float) -> None:
    """配合 ``perf_now`` 使用,记录从 ``start`` 到现在的耗时。

    :param name: 阶段名称
    :param start: ``perf_now()`` 返回的起始时间戳

    用法::

        s = perf_now()
        ...  # 耗时操作
        perf_log("my_op", s)
    """
    if not is_perf_enabled():
        return
    elapsed_ms = (perf_now() - start) * 1000.0
    logger.info(f"[perf] {name}: {elapsed_ms:.2f}ms")


__all__ = [
    "is_perf_enabled",
    "log_stage",
    "perf_log",
    "perf_now",
    "timed",
]
