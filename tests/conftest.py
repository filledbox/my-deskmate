


import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _never_touch_the_real_db(monkeypatch, tmp_path):


    from backend import memory

    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "guard.db"))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):

    from backend import memory

    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(memory.SCHEMA)
    memory.insert_video("v1", "测试视频", "local", None, "", duration_seconds=600.0)
    return db_path


@pytest.fixture
def no_chroma(monkeypatch):

    from backend import memory

    calls: list[tuple] = []

    def fake_reindex(video_id, span_start, span_end, source_type="speech"):
        calls.append((video_id, span_start, span_end, source_type))
        return 0

    monkeypatch.setattr(memory, "_reindex_windows", fake_reindex)
    return calls
