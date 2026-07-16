from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime


LATEST_SCHEMA_VERSION = 4

PROJECT_MODULE_DEFAULTS = {
    "practice": {"mock": 0, "plan": 0, "tasks": 0, "readiness": 0},
    "exam_prep": {"mock": 1, "plan": 1, "tasks": 0, "readiness": 1},
    "practical_certification": {"mock": 1, "plan": 1, "tasks": 1, "readiness": 1},
}

PROJECT_SETTING_DEFAULTS = {
    "mock_threshold": "85",
    "chapter_threshold": "75",
    "task_threshold": "80",
    "qualifying_count": "50",
    "qualifying_minutes": "60",
    "gate_mock_enabled": "1",
    "gate_chapter_enabled": "1",
    "gate_task_enabled": "1",
}


LATEST_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_projects (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  project_type TEXT NOT NULL DEFAULT 'practice',
  status TEXT NOT NULL DEFAULT 'active',
  description TEXT NOT NULL DEFAULT '',
  start_date TEXT NOT NULL,
  duration_weeks INTEGER NOT NULL DEFAULT 12,
  practice_alias TEXT NOT NULL DEFAULT '实践任务',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_modules (
  project_id INTEGER NOT NULL REFERENCES learning_projects(id) ON DELETE CASCADE,
  module_key TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(project_id,module_key)
);
CREATE TABLE IF NOT EXISTS project_settings (
  project_id INTEGER NOT NULL REFERENCES learning_projects(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(project_id,key)
);
CREATE TABLE IF NOT EXISTS subjects (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  name TEXT NOT NULL,
  code TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_subject_project_code
  ON subjects(project_id,code) WHERE code<>'';
CREATE TABLE IF NOT EXISTS chapters (
  id INTEGER PRIMARY KEY,
  subject_id INTEGER NOT NULL REFERENCES subjects(id),
  name TEXT NOT NULL,
  is_core INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS knowledge_points (
  id INTEGER PRIMARY KEY,
  chapter_id INTEGER NOT NULL REFERENCES chapters(id),
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_documents (
  id TEXT PRIMARY KEY,
  project_id INTEGER REFERENCES learning_projects(id),
  subject_id INTEGER REFERENCES subjects(id),
  original_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_jobs (
  id TEXT PRIMARY KEY,
  source_document_id TEXT NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  project_id INTEGER REFERENCES learning_projects(id),
  subject_id INTEGER REFERENCES subjects(id),
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  parser_version TEXT NOT NULL DEFAULT '',
  detected_json TEXT NOT NULL DEFAULT '{}',
  error_json TEXT NOT NULL DEFAULT '[]',
  preview_json TEXT NOT NULL DEFAULT '[]',
  candidate_count INTEGER NOT NULL DEFAULT 0,
  valid_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  committed_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS import_candidates (
  id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
  item_index INTEGER NOT NULL,
  source_page INTEGER,
  source_item_key TEXT NOT NULL DEFAULT '',
  question_type TEXT NOT NULL,
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '[]',
  answer_json TEXT NOT NULL DEFAULT '[]',
  explanation TEXT NOT NULL DEFAULT '',
  chapter_name TEXT NOT NULL DEFAULT '',
  point_name TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  duplicate_question_id INTEGER REFERENCES questions(id),
  validation_error TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT 'insert',
  subject_key TEXT NOT NULL DEFAULT '',
  subject_name TEXT NOT NULL DEFAULT '',
  subject_code TEXT NOT NULL DEFAULT '',
  chapter_key TEXT NOT NULL DEFAULT '',
  chapter_is_core INTEGER NOT NULL DEFAULT 0,
  point_key TEXT NOT NULL DEFAULT '',
  difficulty INTEGER NOT NULL DEFAULT 2,
  sender_status TEXT NOT NULL DEFAULT 'draft',
  image_ref TEXT NOT NULL DEFAULT '',
  image_missing INTEGER NOT NULL DEFAULT 0,
  UNIQUE(job_id,item_index)
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY,
  knowledge_point_id INTEGER NOT NULL REFERENCES knowledge_points(id),
  type TEXT NOT NULL,
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '[]',
  answer_json TEXT NOT NULL DEFAULT '[]',
  explanation TEXT NOT NULL DEFAULT '',
  difficulty INTEGER NOT NULL DEFAULT 2,
  source TEXT NOT NULL DEFAULT '',
  version_note TEXT NOT NULL DEFAULT '',
  image_path TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  source_document_id TEXT REFERENCES source_documents(id),
  source_page INTEGER,
  source_item_key TEXT NOT NULL DEFAULT '',
  import_batch_id TEXT,
  parser_version TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS question_progress (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  mastery_level INTEGER NOT NULL DEFAULT 0,
  due_date TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  correct_attempts INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  mode TEXT NOT NULL,
  is_correct INTEGER,
  self_rating TEXT,
  answered_at TEXT NOT NULL,
  session_id TEXT
);
CREATE TABLE IF NOT EXISTS practice_sessions (
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  mode TEXT NOT NULL,
  question_ids_json TEXT NOT NULL,
  current_index INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  state_json TEXT NOT NULL DEFAULT '{}',
  paused_at TEXT,
  terminated_at TEXT,
  control_token TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS labs (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  chapter_id INTEGER NOT NULL REFERENCES chapters(id),
  title TEXT NOT NULL,
  objective TEXT NOT NULL DEFAULT '',
  topology_file_path TEXT NOT NULL DEFAULT '',
  commands TEXT NOT NULL DEFAULT '',
  verification TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  image_path TEXT,
  status TEXT NOT NULL DEFAULT 'planned',
  due_date TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_plans (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  week_no INTEGER NOT NULL,
  title TEXT NOT NULL,
  chapter_goal TEXT NOT NULL DEFAULT '',
  question_goal INTEGER NOT NULL DEFAULT 0,
  lab_goal INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  UNIQUE(project_id,week_no)
);
CREATE TABLE IF NOT EXISTS mock_exams (
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  started_at TEXT NOT NULL,
  submitted_at TEXT,
  question_ids_json TEXT NOT NULL,
  answers_json TEXT NOT NULL DEFAULT '{}',
  score REAL,
  objective_count INTEGER NOT NULL,
  time_limit INTEGER NOT NULL,
  qualifying INTEGER NOT NULL DEFAULT 0,
  chapter_ids_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  remaining_seconds INTEGER NOT NULL DEFAULT 0,
  active_started_at TEXT,
  paused_at TEXT,
  terminated_at TEXT,
  control_token TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_jobs (
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES learning_projects(id),
  scope_type TEXT NOT NULL,
  scope_id INTEGER,
  include_drafts INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  question_count INTEGER NOT NULL DEFAULT 0,
  image_count INTEGER NOT NULL DEFAULT 0,
  missing_image_count INTEGER NOT NULL DEFAULT 0,
  warning_json TEXT NOT NULL DEFAULT '[]',
  error_json TEXT NOT NULL DEFAULT '[]',
  stored_path TEXT,
  filename TEXT,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  expires_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_subjects_project ON subjects(project_id);
CREATE INDEX IF NOT EXISTS ix_questions_status ON questions(status);
CREATE INDEX IF NOT EXISTS ix_questions_import_batch ON questions(import_batch_id);
CREATE INDEX IF NOT EXISTS ix_question_progress_due ON question_progress(due_date);
CREATE INDEX IF NOT EXISTS ix_practice_sessions_project ON practice_sessions(project_id,completed_at);
CREATE INDEX IF NOT EXISTS ix_mock_exams_project ON mock_exams(project_id,submitted_at);
CREATE INDEX IF NOT EXISTS ix_import_jobs_project ON import_jobs(project_id,created_at);
CREATE INDEX IF NOT EXISTS ix_import_candidates_job ON import_candidates(job_id,item_index);
CREATE INDEX IF NOT EXISTS ix_export_jobs_project ON export_jobs(project_id,created_at);
"""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _seed_project_configuration(conn: sqlite3.Connection, project_id: int, project_type: str) -> None:
    defaults = PROJECT_MODULE_DEFAULTS.get(project_type, PROJECT_MODULE_DEFAULTS["practice"])
    for key, enabled in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO project_modules(project_id,module_key,enabled) VALUES (?,?,?)",
            (project_id, key, enabled),
        )
    for key, value in PROJECT_SETTING_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO project_settings(project_id,key,value) VALUES (?,?,?)",
            (project_id, key, value),
        )


def create_project(
    conn: sqlite3.Connection,
    name: str,
    project_type: str = "practice",
    start_date: str | None = None,
    duration_weeks: int = 12,
    practice_alias: str = "实践任务",
) -> int:
    now = _now()
    project_id = conn.execute(
        """INSERT INTO learning_projects(name,project_type,status,start_date,duration_weeks,
           practice_alias,created_at,updated_at) VALUES (?,?, 'active',?,?,?,?,?)""",
        (name, project_type, start_date or date.today().isoformat(), duration_weeks, practice_alias, now, now),
    ).lastrowid
    _seed_project_configuration(conn, project_id, project_type)
    return int(project_id)


def _create_empty_database(conn: sqlite3.Connection) -> None:
    conn.executescript(LATEST_SCHEMA)
    project_id = create_project(conn, "我的学习项目", "practice")
    subject_id = conn.execute(
        "INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)", (project_id, "默认科目", "")
    ).lastrowid
    chapter_id = conn.execute(
        "INSERT INTO chapters(subject_id,name,is_core) VALUES (?,?,1)", (subject_id, "待分类")
    ).lastrowid
    conn.execute("INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter_id, "待分类"))


def _project_for_question_ids(conn: sqlite3.Connection, raw_ids: str, fallback: int) -> int:
    try:
        ids = [int(value) for value in json.loads(raw_ids)]
    except (TypeError, ValueError, json.JSONDecodeError):
        ids = []
    if ids:
        row = conn.execute(
            """SELECT s.project_id FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
               JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE q.id=?""",
            (ids[0],),
        ).fetchone()
        if row:
            return int(row[0])
    return fallback


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    now = _now()
    old_settings = {row[0]: row[1] for row in conn.execute("SELECT key,value FROM settings")}

    conn.execute("ALTER TABLE certifications RENAME TO learning_projects")
    for definition in (
        "project_type TEXT NOT NULL DEFAULT 'practical_certification'",
        "status TEXT NOT NULL DEFAULT 'active'",
        "description TEXT NOT NULL DEFAULT ''",
        f"start_date TEXT NOT NULL DEFAULT '{date.today().isoformat()}'",
        "duration_weeks INTEGER NOT NULL DEFAULT 12",
        "practice_alias TEXT NOT NULL DEFAULT 'HCL实验'",
        f"created_at TEXT NOT NULL DEFAULT '{now}'",
        f"updated_at TEXT NOT NULL DEFAULT '{now}'",
    ):
        _add_column(conn, "learning_projects", definition)
    conn.execute(
        """UPDATE learning_projects SET name='H3CSE',project_type='practical_certification',status='active',
           start_date=?,duration_weeks=12,practice_alias='HCL实验',updated_at=?""",
        (old_settings.get("plan_start_date", date.today().isoformat()), now),
    )

    if "certification_id" in _columns(conn, "subjects"):
        conn.execute("ALTER TABLE subjects RENAME COLUMN certification_id TO project_id")
    if "exam_code" in _columns(conn, "subjects"):
        conn.execute("ALTER TABLE subjects RENAME COLUMN exam_code TO code")

    project_id = int(conn.execute("SELECT id FROM learning_projects ORDER BY id LIMIT 1").fetchone()[0])

    conn.executescript("""
      CREATE TABLE IF NOT EXISTS project_modules (
        project_id INTEGER NOT NULL REFERENCES learning_projects(id) ON DELETE CASCADE,
        module_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(project_id,module_key));
      CREATE TABLE IF NOT EXISTS project_settings (
        project_id INTEGER NOT NULL REFERENCES learning_projects(id) ON DELETE CASCADE,
        key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(project_id,key));
    """)
    _seed_project_configuration(conn, project_id, "practical_certification")
    setting_map = {
        "mock_threshold": "mock_threshold",
        "chapter_threshold": "chapter_threshold",
        "lab_threshold": "task_threshold",
        "qualifying_count": "qualifying_count",
        "qualifying_minutes": "qualifying_minutes",
    }
    for old_key, new_key in setting_map.items():
        if old_key in old_settings:
            conn.execute(
                "INSERT OR REPLACE INTO project_settings(project_id,key,value) VALUES (?,?,?)",
                (project_id, new_key, old_settings[old_key]),
            )
    conn.execute("DELETE FROM settings WHERE key<>'intervals'")
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('intervals','3,7,14,30')")
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('max_import_mb','50')")

    if "project_id" not in _columns(conn, "practice_sessions"):
        conn.execute("ALTER TABLE practice_sessions ADD COLUMN project_id INTEGER REFERENCES learning_projects(id)")
        sessions = conn.execute("SELECT id,question_ids_json FROM practice_sessions").fetchall()
        for row in sessions:
            conn.execute(
                "UPDATE practice_sessions SET project_id=? WHERE id=?",
                (_project_for_question_ids(conn, row[1], project_id), row[0]),
            )
    if "project_id" not in _columns(conn, "labs"):
        conn.execute("ALTER TABLE labs ADD COLUMN project_id INTEGER REFERENCES learning_projects(id)")
        conn.execute(
            """UPDATE labs SET project_id=(SELECT s.project_id FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE c.id=labs.chapter_id)"""
        )
    if "project_id" not in _columns(conn, "mock_exams"):
        conn.execute("ALTER TABLE mock_exams ADD COLUMN project_id INTEGER REFERENCES learning_projects(id)")
        exams = conn.execute("SELECT id,question_ids_json FROM mock_exams").fetchall()
        for row in exams:
            conn.execute(
                "UPDATE mock_exams SET project_id=? WHERE id=?",
                (_project_for_question_ids(conn, row[1], project_id), row[0]),
            )

    conn.execute("ALTER TABLE weekly_plans RENAME TO weekly_plans_legacy")
    conn.execute("""CREATE TABLE weekly_plans (
      id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL REFERENCES learning_projects(id),
      week_no INTEGER NOT NULL,title TEXT NOT NULL,chapter_goal TEXT NOT NULL DEFAULT '',
      question_goal INTEGER NOT NULL DEFAULT 0,lab_goal INTEGER NOT NULL DEFAULT 0,
      notes TEXT NOT NULL DEFAULT '',UNIQUE(project_id,week_no))""")
    conn.execute(
        """INSERT INTO weekly_plans(id,project_id,week_no,title,chapter_goal,question_goal,lab_goal,notes)
           SELECT id,?,week_no,title,chapter_goal,question_goal,lab_goal,notes FROM weekly_plans_legacy""",
        (project_id,),
    )
    conn.execute("DROP TABLE weekly_plans_legacy")

    for definition in (
        "source_document_id TEXT REFERENCES source_documents(id)",
        "source_page INTEGER",
        "source_item_key TEXT NOT NULL DEFAULT ''",
        "import_batch_id TEXT",
        "parser_version TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(conn, "questions", definition)
    conn.executescript(LATEST_SCHEMA)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_subject_project_code ON subjects(project_id,code) WHERE code<>''")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    for definition in (
        "import_kind TEXT NOT NULL DEFAULT 'document'",
        "package_json TEXT NOT NULL DEFAULT '{}'",
        "mapping_json TEXT NOT NULL DEFAULT '{}'",
    ):
        _add_column(conn, "import_jobs", definition)
    for definition in (
        "subject_key TEXT NOT NULL DEFAULT ''",
        "subject_name TEXT NOT NULL DEFAULT ''",
        "subject_code TEXT NOT NULL DEFAULT ''",
        "chapter_key TEXT NOT NULL DEFAULT ''",
        "chapter_is_core INTEGER NOT NULL DEFAULT 0",
        "point_key TEXT NOT NULL DEFAULT ''",
        "difficulty INTEGER NOT NULL DEFAULT 2",
        "sender_status TEXT NOT NULL DEFAULT 'draft'",
        "image_ref TEXT NOT NULL DEFAULT ''",
        "image_missing INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(conn, "import_candidates", definition)
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS export_jobs (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES learning_projects(id),
        scope_type TEXT NOT NULL,
        scope_id INTEGER,
        include_drafts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        question_count INTEGER NOT NULL DEFAULT 0,
        image_count INTEGER NOT NULL DEFAULT 0,
        missing_image_count INTEGER NOT NULL DEFAULT 0,
        warning_json TEXT NOT NULL DEFAULT '[]',
        error_json TEXT NOT NULL DEFAULT '[]',
        stored_path TEXT,
        filename TEXT,
        size_bytes INTEGER NOT NULL DEFAULT 0,
        sha256 TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        expires_at TEXT);
      CREATE INDEX IF NOT EXISTS ix_export_jobs_project ON export_jobs(project_id,created_at);
    """)


def _migrate_v3(conn: sqlite3.Connection) -> None:
    already_applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version=3"
    ).fetchone() is not None
    for definition in (
        "status TEXT NOT NULL DEFAULT 'active'",
        "state_json TEXT NOT NULL DEFAULT '{}'",
        "paused_at TEXT",
        "terminated_at TEXT",
        "control_token TEXT NOT NULL DEFAULT ''",
        "updated_at TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(conn, "practice_sessions", definition)
    for definition in (
        "status TEXT NOT NULL DEFAULT 'active'",
        "remaining_seconds INTEGER NOT NULL DEFAULT 0",
        "active_started_at TEXT",
        "paused_at TEXT",
        "terminated_at TEXT",
        "control_token TEXT NOT NULL DEFAULT ''",
        "updated_at TEXT NOT NULL DEFAULT ''",
    ):
        _add_column(conn, "mock_exams", definition)

    if not already_applied:
        now = _now()
        conn.execute("""UPDATE practice_sessions SET status='completed',updated_at=COALESCE(completed_at,started_at,?)
          WHERE completed_at IS NOT NULL""", (now,))
        groups = conn.execute("""SELECT DISTINCT project_id,mode FROM practice_sessions
          WHERE completed_at IS NULL ORDER BY project_id,mode""").fetchall()
        for project_id, mode in groups:
            rows = conn.execute("""SELECT id FROM practice_sessions WHERE project_id=? AND mode=?
              AND completed_at IS NULL ORDER BY started_at DESC,id DESC""", (project_id, mode)).fetchall()
            for index, row in enumerate(rows):
                if index == 0:
                    conn.execute("""UPDATE practice_sessions SET status='paused',paused_at=?,updated_at=?,
                      state_json=COALESCE(NULLIF(state_json,''),'{}'),control_token='' WHERE id=?""",
                      (now, now, row[0]))
                else:
                    conn.execute("""UPDATE practice_sessions SET status='terminated',terminated_at=?,updated_at=?,
                      control_token='' WHERE id=?""", (now, now, row[0]))

        conn.execute("""UPDATE mock_exams SET status='submitted',remaining_seconds=0,
          updated_at=COALESCE(submitted_at,started_at,?) WHERE submitted_at IS NOT NULL""", (now,))
        project_rows = conn.execute("""SELECT DISTINCT project_id FROM mock_exams
          WHERE submitted_at IS NULL ORDER BY project_id""").fetchall()
        for (project_id,) in project_rows:
            rows = conn.execute("""SELECT id,time_limit FROM mock_exams WHERE project_id=? AND submitted_at IS NULL
              ORDER BY started_at DESC,id DESC""", (project_id,)).fetchall()
            for index, row in enumerate(rows):
                if index == 0:
                    conn.execute("""UPDATE mock_exams SET status='paused',remaining_seconds=?,paused_at=?,
                      active_started_at=NULL,updated_at=?,control_token='' WHERE id=?""",
                      (int(row[1]) * 60, now, now, row[0]))
                else:
                    conn.execute("""UPDATE mock_exams SET status='terminated',remaining_seconds=0,
                      terminated_at=?,active_started_at=NULL,updated_at=?,control_token='',answers_json='{}'
                      WHERE id=?""", (now, now, row[0]))

    conn.executescript("""
      CREATE UNIQUE INDEX IF NOT EXISTS ux_practice_active_mode
        ON practice_sessions(project_id,mode) WHERE status IN ('active','paused');
      CREATE UNIQUE INDEX IF NOT EXISTS ux_mock_active_project
        ON mock_exams(project_id) WHERE status IN ('active','paused');
      CREATE INDEX IF NOT EXISTS ix_practice_status ON practice_sessions(project_id,status,mode);
      CREATE INDEX IF NOT EXISTS ix_mock_status ON mock_exams(project_id,status);
    """)


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Add lightweight indexes for larger multi-project question banks."""
    conn.executescript("""
      CREATE INDEX IF NOT EXISTS ix_chapters_subject ON chapters(subject_id);
      CREATE INDEX IF NOT EXISTS ix_points_chapter ON knowledge_points(chapter_id);
      CREATE INDEX IF NOT EXISTS ix_questions_point_status ON questions(knowledge_point_id,status);
      CREATE INDEX IF NOT EXISTS ix_attempts_question_time ON attempts(question_id,answered_at);
      CREATE INDEX IF NOT EXISTS ix_attempts_session ON attempts(session_id);
    """)


def migrate_database(conn: sqlite3.Connection) -> int:
    """Upgrade an empty or legacy database in-place and return its schema version."""
    conn.execute("PRAGMA foreign_keys=OFF")
    tables = _tables(conn)
    if not tables or tables <= {"schema_migrations"}:
        _create_empty_database(conn)
    elif "certifications" in tables:
        _migrate_legacy(conn)
    else:
        conn.executescript(LATEST_SCHEMA)

    _migrate_v2(conn)
    _migrate_v3(conn)
    _migrate_v4(conn)

    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (?,?)",
        (LATEST_SCHEMA_VERSION, _now()),
    )
    conn.execute("UPDATE practice_sessions SET project_id=(SELECT id FROM learning_projects ORDER BY id LIMIT 1) WHERE project_id IS NULL")
    conn.execute("UPDATE labs SET project_id=(SELECT id FROM learning_projects ORDER BY id LIMIT 1) WHERE project_id IS NULL")
    conn.execute("UPDATE mock_exams SET project_id=(SELECT id FROM learning_projects ORDER BY id LIMIT 1) WHERE project_id IS NULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return LATEST_SCHEMA_VERSION
