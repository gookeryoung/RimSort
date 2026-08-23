"""Single-instance lock to prevent multiple RimSort processes."""

from pathlib import Path

from loguru import logger
from PySide6.QtCore import QLockFile


class SingleInstanceLock:
    """Prevents multiple instances of RimSort from running simultaneously.

    Wraps QLockFile to manage a PID-based lock file with stale lock recovery.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._lock_file = QLockFile(str(lock_path))

    def acquire(self) -> bool:
        """Attempt to acquire the single-instance lock.

        :return: True if the lock was acquired, False if another instance is running.
        """
        # 用短超时而非零等待: Windows 上杀毒/搜索索引器可能在锁文件创建瞬间
        # 短暂占用文件句柄, tryLock(0) 零容忍会把这种瞬时竞争误判为
        # "已有实例运行", 3 秒缓冲可容忍瞬时占用后再判定
        if self._lock_file.tryLock(3000):
            logger.info(f"Single-instance lock acquired: {self._lock_path}")
            return True

        pid, hostname, appname = self._lock_file.getLockInfo()
        logger.warning(
            f"Lock held by PID {pid} on {hostname} ({appname}), "
            "attempting stale lock recovery"
        )

        if self._lock_file.removeStaleLockFile():
            logger.info("Stale lock file removed, retrying")
            if self._lock_file.tryLock(0):
                logger.info(
                    f"Single-instance lock acquired after stale recovery: "
                    f"{self._lock_path}"
                )
                return True

        logger.warning(f"Another instance is already running (PID {pid})")
        return False

    def release(self) -> None:
        """Release the lock and delete the lock file."""
        if self._lock_file.isLocked():
            self._lock_file.unlock()
            logger.info(f"Single-instance lock released: {self._lock_path}")
