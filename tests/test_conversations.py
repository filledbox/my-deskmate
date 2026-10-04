


import sqlite3

import pytest

from backend import memory


@pytest.fixture
def conv(tmp_db):

    return memory.create_conversation("v1", "测试会话")["id"]


def _msg(conv_id, role="user", text="内容", video="v1"):
    return memory.insert_chat_message(video, role, text, 1.0, conversation_id=conv_id)


def test_derive_title_truncates_with_ellipsis():
    title = memory.derive_title("问" * 50)
    assert len(title) == memory.CONVERSATION_TITLE_MAX_CHARS + 1
    assert title.endswith("…")


def test_derive_title_keeps_short_question_verbatim():
    assert memory.derive_title("刚才那个矩阵为什么可逆") == "刚才那个矩阵为什么可逆"


def test_derive_title_collapses_newlines_and_runs_of_space():
    assert memory.derive_title("第一行\n\n  第二行\t第三行") == "第一行 第二行 第三行"


def test_derive_title_falls_back_on_blank():
    assert memory.derive_title("") == "新对话"
    assert memory.derive_title("   \n ") == "新对话"
    assert memory.derive_title(None) == "新对话"


def test_derive_title_never_splits_a_character():

    title = memory.derive_title("🎓" * 40)
    assert title == "🎓" * memory.CONVERSATION_TITLE_MAX_CHARS + "…"


def test_create_conversation_mints_8_char_id_and_no_messages(tmp_db):
    conv_id = memory.create_conversation("v1", "标题")["id"]
    assert len(conv_id) == 8
    assert memory.get_conversation(conv_id)["title"] == "标题"
    listed = memory.list_conversations("v1")
    assert len(listed) == 1 and listed[0]["message_count"] == 0


def test_create_conversation_never_mints_a_legacy_prefixed_id(tmp_db):

    assert not memory.create_conversation("v1")["id"].startswith("legacy_")


def test_get_conversation_missing_is_none(tmp_db):
    assert memory.get_conversation("nope") is None


def test_get_conversation_for_video_rejects_cross_video_id(tmp_db, conv):
    memory.insert_video("v2", "另一支", "local", None, "", duration_seconds=60.0)
    assert memory.get_conversation_for_video("v1", conv) is not None
    assert memory.get_conversation_for_video("v2", conv) is None


def test_insert_chat_message_requires_conversation_id_as_keyword(conv):


    with pytest.raises(TypeError):
        memory.insert_chat_message("v1", "user", "问题", 1.0)


def test_insert_chat_message_rejects_conversation_from_another_video(tmp_db, conv):
    memory.insert_video("v2", "另一支", "local", None, "", duration_seconds=60.0)
    with pytest.raises(ValueError):
        memory.insert_chat_message("v2", "user", "越权", 1.0, conversation_id=conv)


def test_insert_chat_message_bumps_only_its_own_conversation(tmp_db):
    a = memory.create_conversation("v1", "A")["id"]
    b = memory.create_conversation("v1", "B")["id"]
    before_b = memory.get_conversation(b)["updated_at"]
    _msg(a)
    assert memory.get_conversation(a)["updated_at"] >= memory.get_conversation(a)["created_at"]
    assert memory.get_conversation(b)["updated_at"] == before_b


def test_get_conversation_messages_is_scoped(tmp_db):
    a = memory.create_conversation("v1", "A")["id"]
    b = memory.create_conversation("v1", "B")["id"]
    _msg(a, "user", "A 的问题")
    _msg(b, "user", "B 的问题")
    assert [m["content"] for m in memory.get_conversation_messages(a)] == ["A 的问题"]
    assert [m["content"] for m in memory.get_conversation_messages(b)] == ["B 的问题"]


def test_recent_chat_messages_do_not_leak_across_conversations(tmp_db):

    a = memory.create_conversation("v1", "A")["id"]
    b = memory.create_conversation("v1", "B")["id"]
    _msg(a, "user", "A 的问题")
    _msg(b, "user", "B 的问题")
    assert [m["content"] for m in
            memory.get_recent_chat_messages("v1", conversation_id=a)] == ["A 的问题"]


def test_recent_chat_messages_is_ascending_and_limited(tmp_db):
    c = memory.create_conversation("v1", "C")["id"]
    for i in range(5):
        _msg(c, "user", f"第{i}条")
    got = memory.get_recent_chat_messages("v1", limit=2, conversation_id=c)

    assert [m["content"] for m in got] == ["第3条", "第4条"]


def test_get_conversation_messages_limit_takes_the_tail(tmp_db):
    c = memory.create_conversation("v1", "C")["id"]
    for i in range(5):
        _msg(c, "user", f"第{i}条")
    assert [m["content"] for m in memory.get_conversation_messages(c, limit=2)] == ["第3条", "第4条"]
    assert len(memory.get_conversation_messages(c)) == 5


def test_list_conversations_puts_empty_conversation_last(tmp_db):

    old = memory.create_conversation("v1", "聊过的")["id"]
    _msg(old)
    memory.create_conversation("v1", "刚建的空会话")
    assert [c["title"] for c in memory.list_conversations("v1")] == ["聊过的", "刚建的空会话"]


def test_list_conversations_orders_by_last_message(tmp_db):
    a = memory.create_conversation("v1", "A")["id"]
    b = memory.create_conversation("v1", "B")["id"]
    _msg(a, text="让 A 先有消息")
    _msg(b, text="B 后写的")
    assert [c["title"] for c in memory.list_conversations("v1")] == ["B", "A"]


def test_list_conversations_counts_messages_and_isolates_by_video(tmp_db):
    memory.insert_video("v2", "另一支", "local", None, "", duration_seconds=60.0)
    a = memory.create_conversation("v1", "A")["id"]
    _msg(a, "user")
    _msg(a, "assistant")
    memory.create_conversation("v2", "别家视频的")
    listed = memory.list_conversations("v1")
    assert len(listed) == 1
    assert listed[0]["message_count"] == 2


def test_delete_conversation_removes_only_its_own_messages(tmp_db):
    a = memory.create_conversation("v1", "A")["id"]
    b = memory.create_conversation("v1", "B")["id"]
    _msg(a, "user")
    _msg(a, "assistant")
    _msg(b, "user")
    assert memory.delete_conversation("v1", a) == 2
    assert memory.get_conversation_messages(a) == []
    assert len(memory.get_conversation_messages(b)) == 1
    assert [c["title"] for c in memory.list_conversations("v1")] == ["B"]


def test_delete_conversation_wrong_video_is_none_and_deletes_nothing(tmp_db):
    memory.insert_video("v2", "另一支", "local", None, "", duration_seconds=60.0)
    a = memory.create_conversation("v1", "A")["id"]
    _msg(a)
    assert memory.delete_conversation("v2", a) is None
    assert len(memory.get_conversation_messages(a)) == 1


_LEGACY_VIDEOS_DDL = """
CREATE TABLE videos (
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
"""


_LEGACY_CHAT_DDL = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    playback_position REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _build_legacy_db(path, rows):


    with sqlite3.connect(path) as conn:
        conn.executescript(_LEGACY_VIDEOS_DDL)
        conn.executescript(_LEGACY_CHAT_DDL)
        for vid, title in (("v1", "甲"), ("v2", "乙")):
            conn.execute(
                "INSERT INTO videos (id, title, source_type, file_path) VALUES (?, ?, 'local', '')",
                (vid, title),
            )
        conn.executemany(
            "INSERT INTO chat_messages (video_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def _point_at(monkeypatch, path):
    monkeypatch.setattr(memory, "DB_PATH", str(path))


def test_migration_backfills_one_legacy_conversation_per_video(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, [
        ("v1", "user", "甲的问题一", "2026-08-16 14:36:23"),
        ("v1", "assistant", "甲的回答", "2026-08-16 14:36:30"),
        ("v2", "user", "乙的问题", "2026-09-15 17:47:48"),
    ])
    _point_at(monkeypatch, db)

    memory.init_db()

    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")}
        assert "conversation_id" in cols
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE conversation_id IS NULL").fetchone()[0] == 0
        convs = conn.execute(
            "SELECT id, video_id, title, updated_at FROM conversations ORDER BY video_id"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]

    assert total == 3
    assert [(c[0], c[1], c[2]) for c in convs] == [
        ("legacy_v1", "v1", "历史对话"),
        ("legacy_v2", "v2", "历史对话"),
    ]


def test_migration_sets_updated_at_to_last_message_not_now(tmp_path, monkeypatch):

    db = tmp_path / "legacy.db"
    _build_legacy_db(db, [
        ("v1", "user", "老问题", "2026-08-16 14:36:23"),
        ("v1", "assistant", "老回答", "2026-08-16 14:36:30"),
    ])
    _point_at(monkeypatch, db)
    memory.init_db()

    conv = memory.list_conversations("v1")[0]
    assert conv["updated_at"] == "2026-08-16 14:36:30"

    new_id = memory.create_conversation("v1", "新会话")["id"]
    _msg(new_id, "user", "新问题")
    assert [c["title"] for c in memory.list_conversations("v1")] == ["新会话", "历史对话"]


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, [("v1", "user", "老问题", "2026-08-16 14:36:23")])
    _point_at(monkeypatch, db)

    memory.init_db()
    memory.init_db()
    memory._migrate_conversations()

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1


def test_migration_leaves_fresh_db_untouched(tmp_db):

    memory.init_db()
    assert memory.list_conversations("v1") == []


def test_conversation_ids_do_not_collide_over_many_calls(tmp_db):

    ids = {memory.create_conversation("v1", f"c{i}")["id"] for i in range(200)}
    assert len(ids) == 200
