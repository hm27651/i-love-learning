from __future__ import annotations

import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from services.transfer.transfer_common import (
    IMAGE_SUFFIXES,
    MANIFEST_COMMENT_PREFIX,
    MAX_COMPRESSION_RATIO,
    MAX_EXPANDED_BYTES,
    MAX_PACKAGE_FILES,
    PACKAGE_FORMAT,
    PACKAGE_VERSION,
    QUESTION_TYPES,
    _sha256_bytes,
)


def _validate_zip_path(name: str) -> None:
    if "\\" in name:
        raise ValueError("ZIP 包含非法路径分隔符")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("ZIP 包含不安全路径")


def _validate_question(
    record: dict,
    subject_keys: set[str],
    chapter_subjects: dict[str, str],
    point_chapters: dict[str, str],
    files: set[str],
) -> None:
    required = {"item_key", "subject_key", "chapter_key", "point_key", "type", "stem", "options", "answer",
                "explanation", "difficulty", "status", "image_ref", "image_missing"}
    if set(record) != required:
        raise ValueError("题目字段与分享包格式不一致")
    if record["type"] not in QUESTION_TYPES or record["status"] not in {"verified", "draft"}:
        raise ValueError("题型或审核状态无效")
    if not isinstance(record["stem"], str) or not record["stem"].strip():
        raise ValueError("分享包存在空题干")
    if (not isinstance(record["options"], list) or not all(isinstance(value, str) for value in record["options"])
            or not isinstance(record["answer"], list) or not record["answer"]
            or not all(isinstance(value, str) for value in record["answer"])):
        raise ValueError("分享包存在无效答案")
    if not isinstance(record["explanation"], str):
        raise ValueError("题目解析格式无效")
    if (record["subject_key"] not in subject_keys
            or chapter_subjects.get(record["chapter_key"]) != record["subject_key"]
            or point_chapters.get(record["point_key"]) != record["chapter_key"]):
        raise ValueError("题目引用了不存在的知识结构")
    if record["type"] in {"single", "multiple"}:
        labels = set("ABCDEFGH"[:len(record["options"])])
        if len(record["options"]) < 2 or not set(record["answer"]).issubset(labels):
            raise ValueError("客观题答案与选项不一致")
    if isinstance(record["difficulty"], bool) or not isinstance(record["difficulty"], int) or record["difficulty"] not in {1, 2, 3}:
        raise ValueError("题目难度无效")
    if not isinstance(record["image_missing"], bool):
        raise ValueError("题目图片状态无效")
    image_ref = record["image_ref"]
    if not isinstance(image_ref, str) or (record["image_missing"] and image_ref):
        raise ValueError("题目图片引用无效")
    if image_ref and image_ref not in files:
        raise ValueError("题目引用的图片不存在")


def inspect_share_package(path: Path, extract_dir: Path | None = None) -> tuple[dict, list[dict]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP 文件损坏或格式无效") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise ValueError("ZIP 文件数量超过 5000 个")
        names: set[str] = set()
        expanded = 0
        for info in infos:
            _validate_zip_path(info.filename)
            if info.is_dir():
                raise ValueError("ZIP 不允许包含目录项")
            if info.filename in names:
                raise ValueError("ZIP 包含重复路径")
            names.add(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("ZIP 不允许包含符号链接")
            expanded += info.file_size
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError("ZIP 解压后超过 200MB 上限")
            if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ValueError("ZIP 包含异常压缩率文件")
        if "manifest.json" not in names or "questions.json" not in names:
            raise ValueError("ZIP 缺少 manifest.json 或 questions.json")
        try:
            manifest_bytes = archive.read("manifest.json")
            questions_bytes = archive.read("questions.json")
            expected_comment = MANIFEST_COMMENT_PREFIX + _sha256_bytes(manifest_bytes).encode("ascii")
            if archive.comment != expected_comment:
                raise ValueError("manifest.json 完整性校验失败，分享包可能已被修改")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            questions = json.loads(questions_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("分享包 JSON 无法解析") from exc
        if manifest.get("format") != PACKAGE_FORMAT or manifest.get("version") != PACKAGE_VERSION:
            raise ValueError("不支持的分享包格式或版本")
        required_manifest = {"format", "version", "created_at", "project", "scope", "include_drafts",
                             "counts", "subjects", "warnings", "files"}
        if set(manifest) != required_manifest:
            raise ValueError("manifest.json 字段与分享包格式不一致")
        project = manifest.get("project")
        scope = manifest.get("scope")
        counts = manifest.get("counts")
        if (not isinstance(project, dict) or set(project) != {"name", "type"}
                or not isinstance(project["name"], str) or not project["name"].strip()
                or not isinstance(project["type"], str)):
            raise ValueError("分享包项目建议无效")
        if (not isinstance(scope, dict) or set(scope) != {"type", "label"}
                or scope["type"] not in {"project", "subject", "chapter"}
                or not isinstance(scope["label"], str)):
            raise ValueError("分享包范围无效")
        if (not isinstance(counts, dict) or set(counts) != {"questions", "images", "missing_images"}
                or any(isinstance(counts[key], bool) or not isinstance(counts[key], int) or counts[key] < 0 for key in counts)):
            raise ValueError("分享包数量统计无效")
        if not isinstance(manifest.get("include_drafts"), bool) or not isinstance(manifest.get("warnings"), list):
            raise ValueError("分享包设置或警告格式无效")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != names - {"manifest.json"}:
            raise ValueError("分享包文件清单不一致")
        allowed_files = {"questions.json"}
        for name in files:
            path = PurePosixPath(name)
            if name != "questions.json":
                if len(path.parts) != 2 or path.parts[0] != "images" or path.suffix.lower() not in IMAGE_SUFFIXES:
                    raise ValueError(f"分享包包含未知文件：{name}")
                allowed_files.add(name)
        if set(files) != allowed_files:
            raise ValueError("分享包文件清单无效")
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or _sha256_bytes(archive.read(name)) != expected:
                raise ValueError(f"文件校验失败：{name}")
            if name.startswith("images/") and PurePosixPath(name).stem != expected:
                raise ValueError(f"图片内容哈希与文件名不一致：{name}")
        if not isinstance(questions, list) or manifest.get("counts", {}).get("questions") != len(questions):
            raise ValueError("分享包题目数量不一致")
        if counts["images"] != len(files) - 1:
            raise ValueError("分享包图片数量不一致")
        subjects = manifest.get("subjects")
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("分享包缺少科目结构")
        subject_keys: set[str] = set()
        chapter_keys: set[str] = set()
        point_keys: set[str] = set()
        chapter_subjects: dict[str, str] = {}
        point_chapters: dict[str, str] = {}
        for subject in subjects:
            if (not isinstance(subject, dict) or set(subject) != {"key", "name", "code", "chapters"}
                    or not isinstance(subject["key"], str) or not subject["key"]
                    or not isinstance(subject["name"], str) or not subject["name"].strip()
                    or not isinstance(subject["code"], str) or not isinstance(subject["chapters"], list)):
                raise ValueError("科目结构无效")
            if subject["key"] in subject_keys:
                raise ValueError("科目键重复")
            subject_keys.add(subject["key"])
            for chapter in subject["chapters"]:
                if (not isinstance(chapter, dict) or set(chapter) != {"key", "name", "is_core", "points"}
                        or not isinstance(chapter["key"], str) or not chapter["key"]
                        or not isinstance(chapter["name"], str) or not chapter["name"].strip()
                        or not isinstance(chapter["is_core"], bool) or not isinstance(chapter["points"], list)):
                    raise ValueError("章节结构无效")
                if chapter["key"] in chapter_keys:
                    raise ValueError("章节键重复")
                chapter_keys.add(chapter["key"])
                chapter_subjects[chapter["key"]] = subject["key"]
                for point in chapter["points"]:
                    if (not isinstance(point, dict) or set(point) != {"key", "name"}
                            or not isinstance(point["key"], str) or not point["key"]
                            or not isinstance(point["name"], str) or not point["name"].strip()
                            or point["key"] in point_keys):
                        raise ValueError("知识点结构无效")
                    point_keys.add(point["key"])
                    point_chapters[point["key"]] = chapter["key"]
        item_keys: set[str] = set()
        referenced_images: set[str] = set()
        for record in questions:
            if not isinstance(record, dict):
                raise ValueError("题目记录格式无效")
            _validate_question(record, subject_keys, chapter_subjects, point_chapters, set(files))
            if not isinstance(record["item_key"], str) or not record["item_key"] or record["item_key"] in item_keys:
                raise ValueError("题目标识重复")
            item_keys.add(record["item_key"])
            if record["image_ref"]:
                referenced_images.add(record["image_ref"])
        if referenced_images != set(files) - {"questions.json"}:
            raise ValueError("分享包包含未引用图片或缺少图片引用")
        if counts["missing_images"] != sum(1 for record in questions if record["image_missing"]):
            raise ValueError("分享包缺图数量不一致")
        if extract_dir:
            extract_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                if name.startswith("images/"):
                    target = extract_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        return manifest, questions
