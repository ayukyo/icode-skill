#!/usr/bin/env python3
"""Validate ICODE bundled skills and optionally compare installed copies."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RUNTIME_NAMES = {"__pycache__", ".venv", ".cache"}
RUNTIME_FILES = {"config.json", "config.local.json"}
ENTRYPOINT = "SKILL.md.template"
OWNER_MARKER = ".icode-skill-owner.json"


class ValidationError(ValueError):
    pass


def load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"manifest 不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"manifest JSON 解析失败: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValidationError("manifest.schema_version 必须为 1")
    if set(value) != {"schema_version", "skills"}:
        raise ValidationError("manifest 只允许 schema_version/skills 两个顶层字段")
    if not isinstance(value["skills"], list):
        raise ValidationError("manifest.skills 必须是数组")
    return value


def parse_frontmatter(skill_file: Path) -> dict:
    lines = skill_file.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValidationError(f"{skill_file}: 缺少 YAML frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise ValidationError(f"{skill_file}: frontmatter 未闭合") from exc
    values = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValidationError(f"{skill_file}: 无法解析 frontmatter 行: {line!r}")
        key, raw = line.split(":", 1)
        key = key.strip()
        value = raw.strip().strip("\"'")
        if key in values:
            raise ValidationError(f"{skill_file}: frontmatter 字段重复: {key}")
        values[key] = value
    if set(values) != {"name", "description"}:
        raise ValidationError(
            f"{skill_file}: frontmatter 只允许 name/description，实际 {sorted(values)}")
    if not values["description"].startswith("Use when"):
        raise ValidationError(f"{skill_file}: description 必须以 'Use when' 开头")
    return values


def iter_payload_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in RUNTIME_NAMES for part in rel.parts):
            continue
        if path.is_file() and path.name not in RUNTIME_FILES \
                and path.name != OWNER_MARKER \
                and path.suffix not in {".pyc", ".pyo"}:
            yield path, rel


def iter_source_publish_files(root: Path, entrypoint: str):
    for path, rel in iter_payload_files(root):
        publish_rel = Path("SKILL.md") if rel.as_posix() == entrypoint else rel
        yield path, publish_rel


def iter_target_publish_files(root: Path):
    yield from iter_payload_files(root)


def publish_digest(files) -> str:
    digest = hashlib.sha256()
    for path, rel in files:
        digest.update(rel.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def source_digest(source: Path, entrypoint: str) -> str:
    return publish_digest(iter_source_publish_files(source, entrypoint))


def installed_digest(target: Path) -> str:
    return publish_digest(iter_target_publish_files(target))


def compare_source_files(source: Path, entrypoint: str, target: Path) -> None:
    """Compare every publishable source file while allowing target-only files."""
    for source_file, rel in iter_source_publish_files(source, entrypoint):
        target_file = target / rel
        if not target_file.is_file():
            raise ValidationError(f"目标技能缺少文件: {target_file}")
        if hashlib.sha256(source_file.read_bytes()).digest() != \
                hashlib.sha256(target_file.read_bytes()).digest():
            raise ValidationError(f"目标技能文件 hash 不一致: {target_file}")


def validate_skills(manifest_path: Path) -> list[dict]:
    manifest = load_manifest(manifest_path)
    source_root = manifest_path.parent.resolve()
    seen_names = set()
    seen_paths = set()
    rows = []
    for index, item in enumerate(manifest["skills"]):
        if not isinstance(item, dict) or set(item) != {"name", "path", "entrypoint"}:
            raise ValidationError(
                f"skills[{index}] 必须且只能包含 name/path/entrypoint")
        name = item["name"]
        rel_text = item["path"]
        entrypoint = item["entrypoint"]
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValidationError(f"skills[{index}].name 非法: {name!r}")
        if not isinstance(rel_text, str):
            raise ValidationError(f"skills[{index}].path 必须是字符串")
        if entrypoint != ENTRYPOINT:
            raise ValidationError(
                f"skills[{index}].entrypoint 必须是 {ENTRYPOINT!r}")
        rel = PurePosixPath(rel_text)
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 1:
            raise ValidationError(
                f"skills[{index}].path 必须是 manifest 同级的单层相对目录: {rel_text!r}")
        if name in seen_names or rel_text in seen_paths:
            raise ValidationError(f"skills[{index}] 名称或路径重复: {name}/{rel_text}")
        seen_names.add(name)
        seen_paths.add(rel_text)
        source = (source_root / rel_text).resolve()
        if source.parent != source_root or not source.is_dir():
            raise ValidationError(f"技能目录不存在或越界: {source}")
        discoverable = next(source.rglob("SKILL.md"), None)
        if discoverable is not None:
            raise ValidationError(
                f"技能源码包禁止包含可被宿主发现的 SKILL.md: {discoverable}")
        skill_file = source / entrypoint
        if not skill_file.is_file():
            raise ValidationError(f"技能缺少入口模板 {entrypoint}: {source}")
        frontmatter = parse_frontmatter(skill_file)
        if frontmatter["name"] != name:
            raise ValidationError(
                f"manifest 名称 {name!r} 与 SKILL.md name {frontmatter['name']!r} 不一致")
        rows.append({
            "name": name,
            "path": str(source),
            "entrypoint": entrypoint,
            "digest": source_digest(source, entrypoint),
        })
    return rows


def validate_routes(routes_path: Path, skill_names: set[str]) -> list[dict]:
    try:
        value = json.loads(routes_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"routes 不存在: {routes_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"routes JSON 解析失败: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValidationError("routes.schema_version 必须为 1")
    if set(value) != {"schema_version", "routes"} or not isinstance(value["routes"], list):
        raise ValidationError("routes 只允许 schema_version/routes，routes 必须是数组")
    required = {"skill", "triggers", "steps", "input_contract", "output_contract", "fallback"}
    seen = set()
    for index, route in enumerate(value["routes"]):
        if not isinstance(route, dict) or set(route) != required:
            raise ValidationError(f"routes[{index}] 必须且只能包含 {sorted(required)}")
        skill = route["skill"]
        if skill not in skill_names:
            raise ValidationError(f"routes[{index}] 引用了未发布技能: {skill!r}")
        if skill in seen:
            raise ValidationError(f"routes 中技能重复: {skill}")
        seen.add(skill)
        for key in ("triggers", "steps", "input_contract", "output_contract"):
            items = route[key]
            if not isinstance(items, list) or not items \
                    or not all(isinstance(item, str) and item.strip() for item in items):
                raise ValidationError(f"routes[{index}].{key} 必须是非空字符串数组")
        if not isinstance(route["fallback"], str) or not route["fallback"].strip():
            raise ValidationError(f"routes[{index}].fallback 必须是非空字符串")
    return value["routes"]


def compare_targets(rows: list[dict], target_roots: list[Path], allow_extra: bool) -> None:
    for target_root in target_roots:
        for row in rows:
            target = target_root.expanduser().resolve() / row["name"]
            if not (target / "SKILL.md").is_file():
                raise ValidationError(f"目标技能不存在: {target}")
            if allow_extra:
                compare_source_files(
                    Path(row["path"]), row["entrypoint"], target)
                continue
            actual = installed_digest(target)
            if actual != row["digest"]:
                raise ValidationError(
                    f"目标技能 hash 不一致: {target} expected={row['digest']} actual={actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 ICODE bundled skill manifest")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--routes", help="可选 skill-routes.json；校验引用完整性")
    parser.add_argument("--target-root", action="append", default=[])
    parser.add_argument("--allow-extra", action="store_true",
                        help="比较目标时允许 --no-delete 保留的目标独有文件")
    parser.add_argument("--list", action="store_true", help="输出 name<TAB>absolute_path")
    args = parser.parse_args()
    try:
        rows = validate_skills(Path(args.manifest).expanduser().resolve())
        routes = validate_routes(Path(args.routes).expanduser().resolve(),
                                 {row["name"] for row in rows}) if args.routes else []
        compare_targets(rows, [Path(p) for p in args.target_root], args.allow_extra)
    except (OSError, UnicodeError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.list:
        for row in rows:
            print(f"{row['name']}\t{row['path']}\t{row['entrypoint']}")
    else:
        print(json.dumps({"ok": True, "skills": rows, "routes": routes},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
