"""ICODE UI 的只读全局工单目录册与可信身份解析。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


VNEXT_OUT_DIR = re.compile(r"^\.icode_output/(?:\.debug/)?\.icode_output_[0-9]+$")


class CatalogError(RuntimeError):
    """全局索引或工单身份不能被安全解释。"""


@dataclass(frozen=True)
class ResolvedTicket:
    ticket_id: str
    project_path: Path
    out_dir: Path
    status: str
    executable: bool
    generation: str


def _project_id(project_path: Path) -> str:
    digest = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()[:12]
    return f"project-{digest}"


def _read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{label} 不是可读的 UTF-8 JSON") from exc


class TicketCatalog:
    """读取全局索引；只有 ``resolve`` 会返回服务端内部路径。"""

    def __init__(self, index_path: Path, seed_projects=None):
        self.index_path = Path(index_path)
        self.seed_projects = []
        seen = set()
        for raw in seed_projects or []:
            project = Path(raw).expanduser().resolve()
            if project not in seen:
                seen.add(project)
                self.seed_projects.append(project)

    def _project_paths(self, entries: Iterable[Dict]) -> List[Path]:
        projects = list(self.seed_projects)
        seen = set(projects)
        for entry in entries:
            raw = entry.get("project_path")
            if not isinstance(raw, str) or not raw.strip():
                continue
            raw_path = Path(raw).expanduser()
            if not raw_path.is_absolute():
                continue
            project = raw_path.resolve()
            if project not in seen:
                seen.add(project)
                projects.append(project)
        return projects

    def _entries(self) -> List[Dict]:
        if not self.index_path.is_file():
            return []
        root = _read_json(self.index_path, "全局索引")
        if not isinstance(root, dict) or not isinstance(root.get("tickets"), list):
            raise CatalogError("全局索引必须包含 tickets 数组")
        return [item for item in root["tickets"] if isinstance(item, dict)]

    @staticmethod
    def _generation(entry: Dict) -> str:
        return "v3" if entry.get("control_schema_version") == 3 else "legacy"

    @staticmethod
    def _candidate_path(entry: Dict) -> Optional[tuple[Path, Path]]:
        project_raw = entry.get("project_path")
        out_raw = entry.get("out_dir")
        if not isinstance(project_raw, str) or not project_raw.strip():
            return None
        if not isinstance(out_raw, str) or not VNEXT_OUT_DIR.fullmatch(out_raw):
            return None
        raw_project = Path(project_raw).expanduser()
        if not raw_project.is_absolute():
            return None
        project = raw_project.resolve()
        candidate = (project / out_raw).resolve()
        try:
            candidate.relative_to(project)
        except ValueError:
            return None
        return project, candidate

    def _duplicates(self, entries: Iterable[Dict]) -> set[str]:
        counts: Dict[str, int] = {}
        for entry in entries:
            ticket_id = entry.get("ticket_id")
            if isinstance(ticket_id, str) and ticket_id:
                counts[ticket_id] = counts.get(ticket_id, 0) + 1
        return {ticket_id for ticket_id, count in counts.items() if count > 1}

    def snapshot(self, *, project_id: str | None = None,
                 query: str | None = None, status: str | None = None) -> Dict:
        entries = self._entries()
        duplicates = self._duplicates(entries)
        projects: Dict[str, Dict] = {
            _project_id(path): {
                "project_id": _project_id(path),
                "name": path.name or "project",
                "ticket_count": 0,
            }
            for path in self._project_paths(entries)
        }
        tickets: List[Dict] = []
        errors = [
            {"code": "duplicate_ticket_id", "ticket_id": ticket_id}
            for ticket_id in sorted(duplicates)
        ]

        query_norm = (query or "").strip().casefold()
        for entry in entries:
            ticket_id = entry.get("ticket_id")
            project_raw = entry.get("project_path")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                continue
            if not isinstance(project_raw, str) or not project_raw.strip():
                continue
            raw_project = Path(project_raw).expanduser()
            if not raw_project.is_absolute():
                errors.append({"code": "invalid_project_path", "ticket_id": ticket_id})
                continue
            project_path = raw_project.resolve()
            pid = _project_id(project_path)
            projects.setdefault(pid, {
                "project_id": pid,
                "name": project_path.name or "project",
                "ticket_count": 0,
            })
            projects[pid]["ticket_count"] += 1

            generation = self._generation(entry)
            valid_path = self._candidate_path(entry) is not None
            executable = (
                generation == "v3"
                and ticket_id not in duplicates
                and valid_path
                and entry.get("status") not in {"archived", "backup"}
            )
            summary = entry.get("requirement_summary")
            if not isinstance(summary, str):
                summary = ""
            public = {
                "ticket_id": ticket_id,
                "project_id": pid,
                "project_name": project_path.name or "project",
                "status": entry.get("status") if isinstance(entry.get("status"), str) else "unknown",
                "summary": summary,
                "updated_at": entry.get("updated_at") if isinstance(entry.get("updated_at"), str) else "",
                "generation": generation,
                "executable": executable,
            }
            searchable = " ".join((ticket_id, public["project_name"], summary)).casefold()
            if project_id and pid != project_id:
                continue
            if status and public["status"] != status:
                continue
            if query_norm and query_norm not in searchable:
                continue
            tickets.append(public)

        tickets.sort(
            key=lambda item: (item["updated_at"], item["ticket_id"]),
            reverse=True,
        )
        project_list = sorted(projects.values(), key=lambda item: item["name"].casefold())
        return {
            "schema_version": 1,
            "projects": project_list,
            "tickets": tickets,
            "errors": errors,
        }

    def resolve_project(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not project_id.strip():
            raise CatalogError("project_id 必须是非空文本")
        matches = [path for path in self._project_paths(self._entries())
                   if _project_id(path) == project_id]
        if len(matches) != 1:
            raise CatalogError(f"项目 {project_id!r} 不存在或身份不唯一")
        project = matches[0]
        if not project.is_dir() or project == Path("/"):
            raise CatalogError("项目目录不存在或不允许作为 ICODE 工程根")
        return project

    def resolve(self, ticket_id: str) -> ResolvedTicket:
        if not isinstance(ticket_id, str) or not ticket_id.strip():
            raise CatalogError("ticket_id 必须是非空文本")
        matches = [item for item in self._entries() if item.get("ticket_id") == ticket_id]
        if not matches:
            raise CatalogError(f"工单 {ticket_id!r} 不在全局索引中")
        if len(matches) != 1:
            raise CatalogError(f"工单 {ticket_id!r} 在全局索引中不唯一")

        entry = matches[0]
        generation = self._generation(entry)
        if generation != "v3":
            raise CatalogError(f"工单 {ticket_id!r} 是 legacy 条目，只允许只读浏览")
        paths = self._candidate_path(entry)
        if paths is None:
            raise CatalogError(f"工单 {ticket_id!r} 的索引路径不合法")
        project_path, out_dir = paths
        metadata_path = out_dir / ".ico_metadata.json"
        if not metadata_path.is_file():
            raise CatalogError(f"工单 {ticket_id!r} 缺少 metadata")
        metadata = _read_json(metadata_path, "工单 metadata")
        if not isinstance(metadata, dict) or metadata.get("ticket_id") != ticket_id:
            raise CatalogError(f"工单 {ticket_id!r} 的索引与 metadata 身份不一致")
        status = entry.get("status")
        if not isinstance(status, str):
            status = "unknown"
        executable = status not in {"archived", "backup"}
        return ResolvedTicket(
            ticket_id=ticket_id,
            project_path=project_path,
            out_dir=out_dir,
            status=status,
            executable=executable,
            generation=generation,
        )

    def public_ticket(self, ticket_id: str) -> Dict:
        """返回不含路径的单工单索引投影；重复 ID 仍 fail-closed。"""
        matches = [item for item in self.snapshot()["tickets"]
                   if item.get("ticket_id") == ticket_id]
        if not matches:
            raise CatalogError(f"工单 {ticket_id!r} 不在可见目录册中")
        if len(matches) != 1:
            raise CatalogError(f"工单 {ticket_id!r} 在目录册中不唯一")
        return dict(matches[0])
