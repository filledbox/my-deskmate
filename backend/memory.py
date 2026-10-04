import sqlite3
import os
import re
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "mydeskmate.db")
CHROMA_PATH = os.path.join(DATA_DIR, "chroma")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_media_path(path: str | None) -> str | None:


    if not path:
        return None
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(_PROJECT_ROOT, path))


def media_file_exists(path: str | None) -> bool:
    resolved = resolve_media_path(path)
    return bool(resolved) and os.path.exists(resolved)

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    title TEXT,
    source_type TEXT NOT NULL,
    source_url TEXT,
    file_path TEXT NOT NULL,
    duration_seconds REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'downloading', 'preprocessing', 'ready', 'error')),
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS preprocessing_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    stage TEXT NOT NULL,
    processed_up_to_seconds REAL DEFAULT 0,
    total_seconds REAL DEFAULT 0,
    details TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    text TEXT NOT NULL,
    source_type TEXT DEFAULT 'speech',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    title TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    conversation_id TEXT REFERENCES conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    playback_position REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS course_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    title TEXT NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    summary TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keyframes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    timestamp REAL NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chapters_video ON course_chapters(video_id, start_time);
CREATE INDEX IF NOT EXISTS idx_keyframes_video ON keyframes(video_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_transcript_video ON transcript_segments(video_id, start_time);
CREATE INDEX IF NOT EXISTS idx_progress_video ON preprocessing_progress(video_id);
CREATE INDEX IF NOT EXISTS idx_chat_video ON chat_messages(video_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_video ON conversations(video_id, updated_at DESC);
"""

os.makedirs(DATA_DIR, exist_ok=True)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_FK_TABLES = {
    "transcript_segments": "video_id",
    "chat_messages": "video_id",
    "preprocessing_progress": "video_id",
    "course_chapters": "video_id",
    "keyframes": "video_id",
    "conversations": "video_id",
}


def _repair_dangling_fk_to_videos_old():


    with get_db() as conn:
        broken = [
            t for t in _FK_TABLES
            if any(row[2] == "videos_old" for row in conn.execute(f"PRAGMA foreign_key_list({t})").fetchall())
        ]
        if not broken:
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            for t in broken:
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
                ).fetchone()
                if not sql or "videos_old" not in sql[0]:
                    continue
                old_sql = sql[0].replace(f'REFERENCES "videos_old"(id)', 'REFERENCES videos(id)')
                conn.execute(f"ALTER TABLE {t} RENAME TO {t}_old")
                conn.execute(old_sql)
                cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
                colnames = ", ".join(c[1] for c in cols)
                conn.execute(f"INSERT INTO {t} ({colnames}) SELECT {colnames} FROM {t}_old")
                conn.execute(f"DROP TABLE {t}_old")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys=ON")


def _migrate_source_type_check():

    _repair_dangling_fk_to_videos_old()
    with get_db() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='videos'"
        ).fetchone()
        if not row or "CHECK(source_type IN ('youtube', 'local'))" not in (row["sql"] or ""):
            return

        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("ALTER TABLE videos RENAME TO videos_old")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO videos SELECT * FROM videos_old")
        conn.execute("DROP TABLE videos_old")
        conn.commit()


def _migrate_conversations():


    with get_db() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
        if "conversation_id" not in cols:

            conn.execute(
                "ALTER TABLE chat_messages"
                " ADD COLUMN conversation_id TEXT REFERENCES conversations(id)"
            )


        conn.execute(
            """INSERT OR IGNORE INTO conversations (id, video_id, title, created_at, updated_at)
               SELECT 'legacy_' || video_id, video_id, '历史对话', MIN(created_at), MAX(created_at)
               FROM chat_messages WHERE conversation_id IS NULL GROUP BY video_id"""
        )


        conn.execute(
            "UPDATE chat_messages SET conversation_id = 'legacy_' || video_id"
            " WHERE conversation_id IS NULL"
        )


        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_conversation ON chat_messages(conversation_id, id)"
        )
        conn.commit()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
    _migrate_conversations()
    _migrate_source_type_check()


def get_chroma_collection():


    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name="video_segments",
        metadata={"hnsw:space": "cosine"},
    )


def insert_video(video_id: str, title: str, source_type: str,
                 source_url: str | None, file_path: str,
                 duration_seconds: float | None = None) -> dict:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO videos (id, title, source_type, source_url, file_path, duration_seconds, status)
               VALUES (?, ?, ?, ?, ?, ?, 'downloading')""",
            (video_id, title, source_type, source_url, file_path, duration_seconds),
        )
        conn.commit()
    return get_video(video_id)


def update_video_status(video_id: str, status: str, error_message: str | None = None):
    with get_db() as conn:
        conn.execute(
            "UPDATE videos SET status = ?, error_message = ? WHERE id = ?",
            (status, error_message, video_id),
        )
        conn.commit()


def update_video_duration(video_id: str, duration_seconds: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE videos SET duration_seconds = ? WHERE id = ?",
            (duration_seconds, video_id),
        )
        conn.commit()


def update_video_file_path(video_id: str, file_path: str):

    with get_db() as conn:
        conn.execute(
            "UPDATE videos SET file_path = ? WHERE id = ?",
            (file_path, video_id),
        )
        conn.commit()


def get_video(video_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def normalize_video_url(url: str) -> str:


    url = (url or "").strip()
    if not url:
        return url
    base, _, query = url.partition("?")
    base = base.rstrip("/")
    p = None
    if query:
        for kv in query.split("&"):
            k, _, v = kv.partition("=")
            if k == "p" and v:
                p = v
                break
    return f"{base}?p={p}" if p else base


def get_video_by_url(source_url: str) -> dict | None:


    source_url = normalize_video_url(source_url)
    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM videos WHERE source_url = ?
               ORDER BY CASE status
                   WHEN 'ready' THEN 0
                   WHEN 'preprocessing' THEN 1
                   WHEN 'downloading' THEN 2
                   WHEN 'pending' THEN 3
                   ELSE 4
               END, rowid DESC
               LIMIT 1""",
            (source_url,),
        ).fetchone()
    return dict(row) if row else None


def claim_video_retry(video_id: str) -> bool:


    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE videos
               SET status = 'downloading', error_message = NULL
               WHERE id = ? AND status = 'error'""",
            (video_id,),
        )
        conn.commit()
    return cursor.rowcount == 1


def get_preprocessing_videos() -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status IN ('preprocessing', 'downloading')"
        ).fetchall()
    return [dict(r) for r in rows]


def update_progress(video_id: str, stage: str, processed_up_to: float = 0,
                    total: float = 0, details: str = ""):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO preprocessing_progress (video_id, stage, processed_up_to_seconds, total_seconds, details)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, stage, processed_up_to, total, details),
        )
        conn.commit()


def get_latest_progress(video_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM preprocessing_progress WHERE video_id = ? ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
    return dict(row) if row else None


def _normalize_segments(segments: list[dict]) -> list[dict]:


    out = []
    for s in segments:
        text = (s.get("text") or s.get("description") or "").strip()
        if not text:
            continue
        raw_start = s.get("start", s.get("start_time", s.get("timestamp", 0)))
        out.append({
            "start": raw_start,
            "end": s.get("end", s.get("end_time", s.get("timestamp", raw_start))),
            "text": text,
        })
    return out


def _insert_transcript_rows(video_id: str, segments: list[dict], source_type: str = "speech"):
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO transcript_segments (video_id, start_time, end_time, text, source_type)"
            " VALUES (?, ?, ?, ?, ?)",
            [(video_id, s["start"], s["end"], s["text"], source_type) for s in segments],
        )
        conn.commit()


def persist_transcript(video_id: str, segments: list[dict],
                       source_type: str = "speech") -> int:


    rows = _normalize_segments(segments)
    if not rows:
        return 0
    span_start = min(r["start"] for r in rows)
    span_end = max(r["end"] for r in rows)
    _replace_rows_in_span(video_id, span_start, span_end, source_type)
    _insert_transcript_rows(video_id, rows, source_type)
    _reindex_windows(video_id, span_start, span_end, source_type)
    return len(rows)


def get_transcription_coverage(video_id: str) -> float:

    with get_db() as conn:
        row = conn.execute(
            "SELECT MAX(end_time) AS cov FROM transcript_segments WHERE video_id = ?",
            (video_id,),
        ).fetchone()
    return float(row["cov"]) if row and row["cov"] is not None else 0.0


def get_all_transcript_segments(video_id: str) -> list[dict]:

    with get_db() as conn:
        rows = conn.execute(
            "SELECT start_time, end_time, text FROM transcript_segments "
            "WHERE video_id = ? ORDER BY start_time",
            (video_id,),
        ).fetchall()
    return [{"start": r["start_time"], "end": r["end_time"], "text": r["text"]} for r in rows]


def _replace_rows_in_span(video_id: str, from_sec: float, to_sec: float,
                          source_type: str = "speech") -> int:


    with get_db() as conn:
        deleted = conn.execute(
            "DELETE FROM transcript_segments"
            " WHERE video_id = ? AND source_type = ? AND end_time > ? AND start_time <= ?",
            (video_id, source_type, from_sec, to_sec),
        ).rowcount
        conn.commit()
    return deleted


def _reindex_windows(video_id: str, span_start: float, span_end: float,
                     source_type: str = "speech") -> int:


    ws_lo = _window_start(span_start)
    ws_hi = _window_start(span_end)
    upper = ws_hi + INDEX_WINDOW_SECONDS
    with get_db() as conn:
        rows = conn.execute(
            "SELECT start_time, end_time, text FROM transcript_segments"
            " WHERE video_id = ? AND source_type = ? AND start_time >= ? AND start_time < ?"
            " ORDER BY start_time",
            (video_id, source_type, ws_lo, upper),
        ).fetchall()

    get_chroma_collection().delete(where={"$and": [
        {"video_id": video_id}, {"source_type": source_type},
        {"start_time": {"$gte": ws_lo}}, {"start_time": {"$lt": upper}},
    ]})
    return index_segments_chroma(
        video_id,
        [{"start": r["start_time"], "end": r["end_time"], "text": r["text"]} for r in rows],
        source_type,
    )


def insert_chapters(video_id: str, chapters: list[dict]):

    with get_db() as conn:
        conn.execute("DELETE FROM course_chapters WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO course_chapters (video_id, title, start_time, end_time, summary) VALUES (?, ?, ?, ?, ?)",
            [(video_id, c["title"], c["start_time"], c["end_time"], c.get("summary", "")) for c in chapters],
        )
        conn.commit()


def get_chapters(video_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, start_time, end_time, summary FROM course_chapters WHERE video_id = ? ORDER BY start_time",
            (video_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_chapters_at(video_id: str, position: float, lookback: int = 2) -> list[dict]:

    chapters = get_chapters(video_id)
    if not chapters:
        return []
    idx = 0
    for i, c in enumerate(chapters):
        if position >= c["start_time"]:
            idx = i
    return chapters[max(0, idx - lookback): idx + 1]


def insert_keyframes(video_id: str, frames: list[dict]):

    with get_db() as conn:
        conn.execute("DELETE FROM keyframes WHERE video_id = ?", (video_id,))
        conn.executemany(
            "INSERT INTO keyframes (video_id, timestamp, path) VALUES (?, ?, ?)",
            [(video_id, f["timestamp"], f["path"]) for f in frames],
        )
        conn.commit()


def count_keyframes(video_id: str) -> int:

    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM keyframes WHERE video_id = ?", (video_id,)
        ).fetchone()
    return int(row["c"]) if row else 0


INDEX_WINDOW_SECONDS = 60.0


def _window_start(t: float) -> float:


    size = int(INDEX_WINDOW_SECONDS)
    return float((int(t) // size) * size)


def _build_index_units(video_id: str, segments: list[dict],
                       source_type: str) -> list[dict]:


    groups: dict[float, list[dict]] = {}
    for s in segments:
        ts = s.get("start", s.get("timestamp", 0))
        groups.setdefault(_window_start(ts), []).append({
            "start": ts,
            "end": s.get("end", s.get("timestamp", ts)),
            "text": s["text"],
        })
    units = []
    for ws in sorted(groups):
        segs = sorted(groups[ws], key=lambda x: x["start"])
        units.append({
            "id": f"{video_id}_{source_type}_w{int(ws * 10)}",

            "document": "".join(x["text"] for x in segs),
            "start_time": ws,
            "end_time": max(x["end"] for x in segs),
        })
    return units


def index_segments_chroma(video_id: str, segments: list[dict],
                          source_type: str = "speech") -> int:


    units = _build_index_units(video_id, _normalize_segments(segments), source_type)
    if not units:
        return 0
    collection = get_chroma_collection()
    collection.upsert(
        ids=[u["id"] for u in units],
        documents=[u["document"] for u in units],
        metadatas=[
            {
                "video_id": video_id,
                "source_type": source_type,
                "start_time": u["start_time"],
                "end_time": u["end_time"],
            }
            for u in units
        ],
    )
    return len(units)


def index_video_transcript(video_id: str, source_type: str = "speech") -> int:


    return index_segments_chroma(video_id, get_all_transcript_segments(video_id), source_type)


def delete_video_index(video_id: str):

    get_chroma_collection().delete(where={"video_id": video_id})


def search_by_time_window(video_id: str, center_time: float,
                          window_seconds: float = 30, limit: int = 2000) -> list[dict]:


    left = max(0, center_time - window_seconds)
    right = center_time + window_seconds
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, video_id, start_time, end_time, text, source_type
               FROM transcript_segments
               WHERE video_id = ? AND start_time BETWEEN ? AND ?
               ORDER BY start_time LIMIT ?""",
            (video_id, left, right, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_transcript_window(
    video_id: str, start_time: float, end_time: float, limit: int = 2000,
) -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, video_id, start_time, end_time, text, source_type
               FROM transcript_segments
               WHERE video_id = ? AND start_time BETWEEN ? AND ?
               ORDER BY start_time LIMIT ?""",
            (video_id, start_time, end_time, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search_semantic(video_id: str, query: str, k: int = 5) -> list[dict]:

    collection = get_chroma_collection()
    results = collection.query(
        query_texts=[query],
        n_results=k,
        where={"video_id": video_id},
    )
    if not results["ids"] or not results["ids"][0]:
        return []
    out = []
    for i, doc_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        out.append({
            "id": doc_id,
            "text": results["documents"][0][i],
            "source_type": meta.get("source_type", "speech"),
            "start_time": meta.get("start_time", 0),
            "end_time": meta.get("end_time", 0),
        })
    return out


def get_keyframes_near(
    video_id: str,
    center_time: float,
    max_frames: int = 3,
) -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, timestamp, path AS image_path
               FROM keyframes
               WHERE video_id = ? AND timestamp <= ?
               ORDER BY timestamp DESC LIMIT ?""",
            (video_id, center_time, max_frames),
        ).fetchall()
    frames = [dict(r) for r in rows]
    frames.reverse()
    return frames


CONVERSATION_TITLE_MAX_CHARS = 30
LEGACY_CONVERSATION_TITLE = "历史对话"


def derive_title(question: str, limit: int = CONVERSATION_TITLE_MAX_CHARS) -> str:


    text = re.sub(r"\s+", " ", question or "").strip()
    if not text:
        return "新对话"
    return text if len(text) <= limit else text[:limit] + "…"


def create_conversation(video_id: str, title: str | None = None) -> dict:
    conv_id = str(uuid.uuid4())[:8]
    with get_db() as conn:
        conn.execute(
            "INSERT INTO conversations (id, video_id, title) VALUES (?, ?, ?)",
            (conv_id, video_id, title),
        )
        conn.commit()
    return get_conversation(conv_id)


def get_conversation(conversation_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return dict(row) if row else None


def get_conversation_for_video(video_id: str, conversation_id: str) -> dict | None:

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        ).fetchone()
    return dict(row) if row else None


def list_conversations(video_id: str) -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.created_at, c.updated_at,
                      COUNT(m.id) AS message_count,
                      COALESCE(MAX(m.id), 0) AS last_message_id
               FROM conversations c
               LEFT JOIN chat_messages m ON m.conversation_id = c.id
               WHERE c.video_id = ?
               GROUP BY c.id
               ORDER BY COALESCE(MAX(m.id), 0) DESC, c.updated_at DESC, c.id DESC""",
            (video_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation_messages(conversation_id: str, limit: int | None = None) -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, playback_position, created_at FROM (
                   SELECT id, role, content, playback_position, created_at
                   FROM chat_messages WHERE conversation_id = ?
                   ORDER BY id DESC LIMIT ?
               ) ORDER BY id""",
            (conversation_id, -1 if limit is None else limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_conversation(video_id: str, conversation_id: str) -> int | None:


    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        ).fetchone()
        if not row:
            return None
        deleted = conn.execute(
            "DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,)
        ).rowcount
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
    return deleted


def insert_chat_message(video_id: str, role: str, content: str,
                        playback_position: float | None = None, *,
                        conversation_id: str):


    with get_db() as conn:
        owned = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND video_id = ?",
            (conversation_id, video_id),
        ).fetchone()
        if not owned:
            raise ValueError(f"会话 {conversation_id} 不属于视频 {video_id}")
        conn.execute(
            "INSERT INTO chat_messages (video_id, conversation_id, role, content, playback_position)"
            " VALUES (?, ?, ?, ?, ?)",
            (video_id, conversation_id, role, content, playback_position),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,),
        )
        conn.commit()


def get_recent_chat_messages(video_id: str, limit: int = 20, *,
                             conversation_id: str) -> list[dict]:


    with get_db() as conn:
        rows = conn.execute(
            """SELECT role, content, playback_position
               FROM chat_messages
               WHERE video_id = ? AND conversation_id = ?
               ORDER BY id DESC LIMIT ?""",
            (video_id, conversation_id, limit),
        ).fetchall()
    messages = [dict(r) for r in rows]
    messages.reverse()
    return messages
