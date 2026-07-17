from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from services.knowledge.knowledge_common import KINDS, KnowledgeDeleteError, normalize_name


def _node(conn: sqlite3.Connection, project_id: int, kind: str, node_id: int):
    if kind == "subject":
        row = conn.execute(
            "SELECT id,name,code,project_id FROM subjects WHERE id=? AND project_id=?", (node_id, project_id)
        ).fetchone()
    elif kind == "chapter":
        row = conn.execute(
            """SELECT c.id,c.name,c.is_core,c.subject_id,s.project_id,s.name subject_name
               FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE c.id=? AND s.project_id=?""",
            (node_id, project_id),
        ).fetchone()
    elif kind == "point":
        row = conn.execute(
            """SELECT kp.id,kp.name,kp.chapter_id,c.subject_id,s.project_id,
                      c.name chapter_name,s.name subject_name
               FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
               JOIN subjects s ON s.id=c.subject_id WHERE kp.id=? AND s.project_id=?""",
            (node_id, project_id),
        ).fetchone()
    else:
        raise KnowledgeDeleteError("无效的知识节点类型")
    if row is None:
        raise KnowledgeDeleteError("知识节点不存在或不属于当前项目")
    return row


def _counts(conn: sqlite3.Connection, kind: str, node_id: int) -> dict[str, int]:
    if kind == "subject":
        row = conn.execute(
            """SELECT
              (SELECT COUNT(*) FROM chapters WHERE subject_id=?) chapters,
              (SELECT COUNT(*) FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id WHERE c.subject_id=?) points,
              (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
                JOIN chapters c ON c.id=kp.chapter_id WHERE c.subject_id=?) questions,
              (SELECT COUNT(*) FROM labs l JOIN chapters c ON c.id=l.chapter_id WHERE c.subject_id=?) tasks,
              (SELECT COUNT(*) FROM source_documents WHERE subject_id=?) source_documents,
              (SELECT COUNT(*) FROM import_jobs WHERE subject_id=?) import_jobs,
              (SELECT COUNT(*) FROM import_jobs WHERE subject_id=? AND status IN ('queued','running')) active_imports""",
            (node_id,) * 7,
        ).fetchone()
        return dict(row)
    if kind == "chapter":
        row = conn.execute(
            """SELECT
              (SELECT COUNT(*) FROM knowledge_points WHERE chapter_id=?) points,
              (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
                WHERE kp.chapter_id=?) questions,
              (SELECT COUNT(*) FROM labs WHERE chapter_id=?) tasks""",
            (node_id, node_id, node_id),
        ).fetchone()
        return dict(row)
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM questions WHERE knowledge_point_id=?) questions", (node_id,)
    ).fetchone()
    return dict(row)


def _targets(conn: sqlite3.Connection, project_id: int, kind: str, source_id: int) -> list[dict]:
    if kind == "subject":
        rows = conn.execute(
            "SELECT id,name,code FROM subjects WHERE project_id=? AND id<>? ORDER BY name,id",
            (project_id, source_id),
        ).fetchall()
        return [dict(row) | {"label": f"{row['code']} · {row['name']}" if row["code"] else row["name"]} for row in rows]
    if kind == "chapter":
        rows = conn.execute(
            """SELECT c.id,c.name,s.name subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE s.project_id=? AND c.id<>? ORDER BY s.name,c.name,c.id""",
            (project_id, source_id),
        ).fetchall()
        return [dict(row) | {"label": f"{row['subject_name']} / {row['name']}"} for row in rows]
    rows = conn.execute(
        """SELECT kp.id,kp.name,c.name chapter_name,s.name subject_name FROM knowledge_points kp
           JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
           WHERE s.project_id=? AND kp.id<>? ORDER BY s.name,c.name,kp.name,kp.id""",
        (project_id, source_id),
    ).fetchall()
    return [dict(row) | {"label": f"{row['subject_name']} / {row['chapter_name']} / {row['name']}"} for row in rows]


def _name_map(rows) -> dict[str, list]:
    result: dict[str, list] = {}
    for row in rows:
        result.setdefault(normalize_name(row["name"]), []).append(row)
    return result


def _duplicate_conflicts(rows, kind: str) -> list[dict]:
    conflicts = []
    for matches in _name_map(rows).values():
        if len(matches) > 1:
            conflicts.append({
                "kind": kind,
                "name": matches[0]["name"],
                "source_id": matches[0]["id"],
                "target_ids": [row["id"] for row in matches[1:]],
                "target_id": None,
                "ambiguous": True,
            })
    return conflicts


def _point_conflicts(conn: sqlite3.Connection, source_chapter_id: int, target_chapter_id: int) -> list[dict]:
    source_points = conn.execute(
        "SELECT id,name FROM knowledge_points WHERE chapter_id=? ORDER BY id", (source_chapter_id,)
    ).fetchall()
    target_points = conn.execute(
        "SELECT id,name FROM knowledge_points WHERE chapter_id=? ORDER BY id", (target_chapter_id,)
    ).fetchall()
    target_map = _name_map(target_points)
    conflicts = _duplicate_conflicts(source_points, "point")
    for source in source_points:
        matches = target_map.get(normalize_name(source["name"]), [])
        if matches:
            conflicts.append({
                "kind": "point",
                "name": source["name"],
                "source_id": source["id"],
                "target_ids": [row["id"] for row in matches],
                "target_id": matches[0]["id"] if len(matches) == 1 else None,
                "ambiguous": len(matches) > 1,
            })
    return conflicts


def _conflicts(
    conn: sqlite3.Connection,
    kind: str,
    source_id: int,
    target_id: int,
) -> list[dict]:
    if kind == "point":
        return []
    if kind == "chapter":
        return _point_conflicts(conn, source_id, target_id)

    source_chapters = conn.execute(
        "SELECT id,name FROM chapters WHERE subject_id=? ORDER BY id", (source_id,)
    ).fetchall()
    target_chapters = conn.execute(
        "SELECT id,name FROM chapters WHERE subject_id=? ORDER BY id", (target_id,)
    ).fetchall()
    target_map = _name_map(target_chapters)
    conflicts = _duplicate_conflicts(source_chapters, "chapter")
    for source in source_chapters:
        source_points = conn.execute(
            "SELECT id,name FROM knowledge_points WHERE chapter_id=? ORDER BY id", (source["id"],)
        ).fetchall()
        conflicts.extend(_duplicate_conflicts(source_points, "point"))
        matches = target_map.get(normalize_name(source["name"]), [])
        if not matches:
            continue
        item = {
            "kind": "chapter",
            "name": source["name"],
            "source_id": source["id"],
            "target_ids": [row["id"] for row in matches],
            "target_id": matches[0]["id"] if len(matches) == 1 else None,
            "ambiguous": len(matches) > 1,
        }
        conflicts.append(item)
        if len(matches) == 1:
            conflicts.extend(_point_conflicts(conn, source["id"], matches[0]["id"]))
    return conflicts


def analyze_delete(
    conn: sqlite3.Connection,
    project_id: int,
    kind: str,
    node_id: int,
    target_id: int | None = None,
) -> dict:
    if kind not in KINDS:
        raise KnowledgeDeleteError("无效的知识节点类型")
    source = _node(conn, project_id, kind, node_id)
    counts = _counts(conn, kind, node_id)
    content_keys = {
        "subject": ("chapters", "points", "questions", "tasks", "source_documents", "import_jobs"),
        "chapter": ("points", "questions", "tasks"),
        "point": ("questions",),
    }[kind]
    nonempty = any(counts.get(key, 0) for key in content_keys)
    target_rows = _targets(conn, project_id, kind, node_id)
    target = None
    conflicts = []
    if target_id is not None:
        target = _node(conn, project_id, kind, target_id)
        if target["id"] == source["id"]:
            raise KnowledgeDeleteError("不能把节点迁移到自身")
        conflicts = _conflicts(conn, kind, node_id, target_id)
    return {
        "kind": kind,
        "source": dict(source),
        "target": dict(target) if target else None,
        "counts": counts,
        "nonempty": nonempty,
        "targets": target_rows,
        "conflicts": conflicts,
        "has_ambiguous_conflicts": any(item["ambiguous"] for item in conflicts),
        "blocked_by_import": bool(counts.get("active_imports", 0)),
    }


def _merge_point(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    conn.execute("UPDATE questions SET knowledge_point_id=?,updated_at=? WHERE knowledge_point_id=?",
                 (target_id, _now(), source_id))
    conn.execute("DELETE FROM knowledge_points WHERE id=?", (source_id,))


def _merge_chapter(conn: sqlite3.Connection, source_id: int, target_id: int) -> None:
    target_points = conn.execute(
        "SELECT id,name FROM knowledge_points WHERE chapter_id=? ORDER BY id", (target_id,)
    ).fetchall()
    target_map = _name_map(target_points)
    source_points = conn.execute(
        "SELECT id,name FROM knowledge_points WHERE chapter_id=? ORDER BY id", (source_id,)
    ).fetchall()
    for source in source_points:
        matches = target_map.get(normalize_name(source["name"]), [])
        if len(matches) > 1:
            raise KnowledgeDeleteError(f"目标章节中存在多个同名知识点：{source['name']}")
        if matches:
            _merge_point(conn, source["id"], matches[0]["id"])
        else:
            conn.execute("UPDATE knowledge_points SET chapter_id=? WHERE id=?", (target_id, source["id"]))
            target_map.setdefault(normalize_name(source["name"]), []).append(source)
    conn.execute("UPDATE labs SET chapter_id=? WHERE chapter_id=?", (target_id, source_id))
    source_core = conn.execute("SELECT is_core FROM chapters WHERE id=?", (source_id,)).fetchone()["is_core"]
    if source_core:
        conn.execute("UPDATE chapters SET is_core=1 WHERE id=?", (target_id,))
    conn.execute("DELETE FROM chapters WHERE id=?", (source_id,))


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _create_backup(conn: sqlite3.Connection, db_path: Path, backup_dir: Path, kind: str, node_id: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = backup_dir / f"knowledge_delete_{timestamp}_{kind}_{node_id}"
    target.mkdir(parents=True, exist_ok=False)
    backup_conn = sqlite3.connect(target / db_path.name)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    return target


def _trim_backups(backup_dir: Path, keep: int = 20) -> None:
    folders = sorted(
        (path for path in backup_dir.glob("knowledge_delete_*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in folders[keep:]:
        shutil.rmtree(path)


def _write_summary(folder: Path, payload: dict) -> None:
    (folder / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_operation_report(backup_dir: Path, payload: dict) -> Path:
    report_dir = backup_dir / "knowledge_operation_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"delete_{datetime.now():%Y%m%d_%H%M%S_%f}_{payload['source']['kind']}_{payload['source']['id']}.json"
    path = report_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def perform_delete(
    conn: sqlite3.Connection,
    project_id: int,
    kind: str,
    node_id: int,
    *,
    target_id: int | None,
    merge_conflicts: bool,
    confirmation_name: str,
    db_path: Path,
    backup_dir: Path,
) -> dict:
    impact = analyze_delete(conn, project_id, kind, node_id, target_id if target_id else None)
    source = impact["source"]
    if impact["blocked_by_import"]:
        raise KnowledgeDeleteError("该科目存在正在运行或排队中的导入任务，请等待完成或先中断任务")
    if impact["nonempty"] and not target_id:
        raise KnowledgeDeleteError("该节点包含内容，必须选择迁移目标")
    if impact["nonempty"] and confirmation_name.strip() != source["name"]:
        raise KnowledgeDeleteError("请输入被删除节点的完整名称进行确认")
    if impact["has_ambiguous_conflicts"]:
        raise KnowledgeDeleteError("当前迁移结构或目标中存在多个同名节点，请先重命名或手工合并后再删除")
    if impact["conflicts"] and not merge_conflicts:
        raise KnowledgeDeleteError("存在同名节点冲突，必须明确确认合并")

    backup_folder = None
    if impact["nonempty"]:
        if conn.in_transaction:
            conn.commit()
        backup_folder = _create_backup(conn, db_path, backup_dir, kind, node_id)

    summary = {
        "operation": "knowledge_migrate_delete" if impact["nonempty"] else "knowledge_empty_delete",
        "started_at": _now(),
        "project_id": project_id,
        "source": {"kind": kind, "id": source["id"], "name": source["name"]},
        "target": ({"id": impact["target"]["id"], "name": impact["target"]["name"]}
                   if impact["target"] else None),
        "counts": impact["counts"],
        "conflicts": {
            "merged_count": len(impact["conflicts"]),
            "chapters": sum(item["kind"] == "chapter" for item in impact["conflicts"]),
            "points": sum(item["kind"] == "point" for item in impact["conflicts"]),
        },
        "backup": str(backup_folder) if backup_folder else None,
    }
    try:
        conn.execute("BEGIN IMMEDIATE")
        if kind == "point":
            if impact["nonempty"]:
                conn.execute("UPDATE questions SET knowledge_point_id=?,updated_at=? WHERE knowledge_point_id=?",
                             (target_id, _now(), node_id))
            conn.execute("DELETE FROM knowledge_points WHERE id=?", (node_id,))
        elif kind == "chapter":
            if impact["nonempty"]:
                _merge_chapter(conn, node_id, int(target_id))
            else:
                conn.execute("DELETE FROM chapters WHERE id=?", (node_id,))
        else:
            if impact["nonempty"]:
                target_chapters = conn.execute(
                    "SELECT id,name FROM chapters WHERE subject_id=? ORDER BY id", (target_id,)
                ).fetchall()
                target_map = _name_map(target_chapters)
                source_chapters = conn.execute(
                    "SELECT id,name FROM chapters WHERE subject_id=? ORDER BY id", (node_id,)
                ).fetchall()
                for chapter in source_chapters:
                    matches = target_map.get(normalize_name(chapter["name"]), [])
                    if len(matches) > 1:
                        raise KnowledgeDeleteError(f"目标科目中存在多个同名章节：{chapter['name']}")
                    if matches:
                        _merge_chapter(conn, chapter["id"], matches[0]["id"])
                    else:
                        conn.execute("UPDATE chapters SET subject_id=? WHERE id=?", (target_id, chapter["id"]))
                        target_map.setdefault(normalize_name(chapter["name"]), []).append(chapter)
                conn.execute("UPDATE source_documents SET subject_id=? WHERE subject_id=?", (target_id, node_id))
                conn.execute("UPDATE import_jobs SET subject_id=? WHERE subject_id=?", (target_id, node_id))
            conn.execute("DELETE FROM subjects WHERE id=?", (node_id,))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        summary["result"] = "failed"
        summary["error"] = str(exc)
        summary["completed_at"] = _now()
        if backup_folder:
            _write_summary(backup_folder, summary)
        else:
            _write_operation_report(backup_dir, summary)
        raise

    summary["result"] = "success"
    summary["completed_at"] = _now()
    if backup_folder:
        _write_summary(backup_folder, summary)
        _trim_backups(backup_dir, keep=20)
    else:
        _write_operation_report(backup_dir, summary)
    return {
        "source": source,
        "target": impact["target"],
        "counts": impact["counts"],
        "backup_folder": backup_folder,
    }
