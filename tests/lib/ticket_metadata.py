"""契约测试共用的工单 metadata 读写适配器。

背景：新工单一律 schema v3，物理 `.ico_metadata.json` 可能是 storage v2 envelope
（`{storage_version, kind, value}`）。测试里直接 `json.load()` 原始文件会读到信封而不是
逻辑字段，得到 KeyError；直接写回逻辑字段则会破坏信封。

这里统一走控制面：读用 `read_metadata_readonly`，写用 `write_metadata`（按原存储模式编码）。
支持传工单目录，也支持传裸 metadata 文件路径（内部复制到临时目录再读）。
"""
import pathlib
import shutil
import sys
import tempfile

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.icode_control import read_events, read_metadata_readonly, write_metadata  # noqa: E402


def _as_dir(path):
    """返回 (工单目录, 是否为临时目录)。"""
    path = pathlib.Path(path)
    if path.is_dir():
        return path, None
    tmp = tempfile.mkdtemp(prefix="icode-meta-")
    shutil.copy(path, pathlib.Path(tmp) / ".ico_metadata.json")
    return pathlib.Path(tmp), tmp


def load(path):
    """读取逻辑 metadata（兼容 inline 与 storage v2 envelope）。"""
    directory, tmp = _as_dir(path)
    try:
        return read_metadata_readonly(directory)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def load_events(ticket_dir):
    """读取事件链的逻辑事件（兼容逐行 storage v2 envelope）。"""
    events, _path = read_events(str(ticket_dir))
    return events


def store(path, value):
    """写回逻辑 metadata；path 必须是工单目录（裸文件无法确定编码目标）。"""
    path = pathlib.Path(path)
    if not path.is_dir():
        raise ValueError("store() 需要工单目录，不能写裸 metadata 文件")
    write_metadata(path, value)