"""cheap-research 监控日志。

写到 ~/.icode/cheap-research.log（用户级，不污染 dev_repo）。
失败 fallback 到 stderr（不影响主流程）。
"""
import logging
import os
import sys
from pathlib import Path


_LOGGER_NAME = "cheap-research"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def _get_log_path() -> Path | None:
    """获取日志路径。失败/无权限返 None（fallback 到 stderr）。"""
    try:
        log_dir = Path.home() / ".icode"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cheap-research.log"
        # 验证可写
        log_file.touch(exist_ok=True)
        return log_file
    except (OSError, PermissionError):
        return None


def get_logger() -> logging.Logger:
    """获取 cheap-research logger（单例，重复调用同实例）。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger  # 已配置

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    # 文件 handler
    log_path = _get_log_path()
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except (OSError, PermissionError):
            pass

    # stderr fallback（不重复）
    if not logger.handlers:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    # 不传播到 root logger
    logger.propagate = False
    return logger


def log_call(tool_name: str, status: str, **fields):
    """记录工具调用。

    Args:
        tool_name: 工具名（如 "summarize"）
        status: "start" / "success" / "error"
        **fields: 额外字段（如 model、tokens_used、cost）
    """
    logger = get_logger()
    extras = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    msg = f"[{tool_name}] {status} {extras}".strip()
    if status == "error":
        logger.warning(msg)
    else:
        logger.info(msg)
