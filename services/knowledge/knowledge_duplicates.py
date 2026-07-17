from __future__ import annotations

import sqlite3

from services.knowledge.knowledge_common import normalize_name


def duplicate_groups(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    groups = []
    chapter_rows = conn.execute(
        """SELECT c.id,c.name,c.subject_id,s.name parent_name FROM chapters c
           JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""",
        (project_id,),
    ).fetchall()
    point_rows = conn.execute(
        """SELECT kp.id,kp.name,kp.chapter_id,c.name parent_name FROM knowledge_points kp
           JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
           WHERE s.project_id=? ORDER BY c.id,kp.id""",
        (project_id,),
    ).fetchall()
    for kind, rows, parent_key in (
        ("chapter", chapter_rows, "subject_id"),
        ("point", point_rows, "chapter_id"),
    ):
        found = {}
        for row in rows:
            key = (row[parent_key], normalize_name(row["name"]))
            found.setdefault(key, []).append(row)
        for values in found.values():
            if len(values) > 1:
                groups.append(
                    {
                        "kind": kind,
                        "name": values[0]["name"],
                        "parent_name": values[0]["parent_name"],
                        "ids": [row["id"] for row in values],
                    }
                )
    return groups


def sibling_name_exists(
    conn: sqlite3.Connection,
    kind: str,
    parent_id: int,
    name: str,
    exclude_id: int | None = None,
) -> bool:
    if kind == "chapter":
        rows = conn.execute("SELECT id,name FROM chapters WHERE subject_id=?", (parent_id,)).fetchall()
    elif kind == "point":
        rows = conn.execute("SELECT id,name FROM knowledge_points WHERE chapter_id=?", (parent_id,)).fetchall()
    else:
        raise ValueError("仅章节和知识点具有父级名称唯一约束")
    wanted = normalize_name(name)
    return any(row["id"] != exclude_id and normalize_name(row["name"]) == wanted for row in rows)

