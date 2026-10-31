


import asyncio

from fastapi.testclient import TestClient

from backend import main, memory


URL = "https://www.bilibili.com/video/BV1Retry"


def _insert_url_video(video_id: str, status: str, *, title: str = "重试课"):
    memory.insert_video(video_id, title, "bilibili", URL, "")
    memory.update_video_status(video_id, status, "old failure" if status == "error" else None)


def _capture_tasks(monkeypatch):
    calls = []

    def capture(coro):
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(main.asyncio, "create_task", capture)
    return calls


def test_get_video_by_url_prefers_usable_record_over_newer_error(tmp_db):
    _insert_url_video("ready-one", "ready")
    _insert_url_video("error-new", "error")
    assert memory.get_video_by_url(URL)["id"] == "ready-one"


def test_error_video_retries_in_place_and_preserves_children(tmp_db, monkeypatch):
    _insert_url_video("failed-one", "error")
    conv = memory.create_conversation("failed-one", "保留的会话")["id"]
    memory.insert_chat_message(
        "failed-one", "user", "不要删除", 12.0, conversation_id=conv
    )
    with memory.get_db() as conn:
        conn.execute(
            """INSERT INTO transcript_segments
               (video_id,start_time,end_time,text) VALUES (?,?,?,?)""",
            ("failed-one", 0, 10, "已有部分转写"),
        )

    async def must_not_probe(*_args, **_kwargs):
        raise AssertionError("error 视频原位重试不应再次 probe 或新建记录")

    monkeypatch.setattr(main, "probe_video", must_not_probe)
    tasks = _capture_tasks(monkeypatch)
    result = asyncio.run(main.create_video(url=URL, file=None))

    assert result == {
        "video_id": "failed-one",
        "title": "重试课",
        "reused": True,
        "retry_started": True,
    }
    assert len(tasks) == 1
    row = memory.get_video("failed-one")
    assert row["status"] == "downloading"
    assert row["error_message"] is None
    with memory.get_db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM videos WHERE source_url = ?", (URL,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM transcript_segments WHERE video_id = 'failed-one'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE video_id = 'failed-one'"
        ).fetchone()[0] == 1


def test_concurrent_error_retries_schedule_pipeline_once(tmp_db, monkeypatch):
    _insert_url_video("failed-one", "error")
    tasks = _capture_tasks(monkeypatch)

    async def submit_twice():
        return await asyncio.gather(
            main.create_video(url=URL, file=None),
            main.create_video(url=URL, file=None),
        )

    results = asyncio.run(submit_twice())
    assert len(tasks) == 1
    assert {r["video_id"] for r in results} == {"failed-one"}
    assert sorted(r["retry_started"] for r in results) == [False, True]


def test_new_video_response_has_stable_reuse_fields(tmp_db, monkeypatch):
    async def fake_probe(*_args, **_kwargs):
        return "BiliBili", "新课"

    monkeypatch.setattr(main, "probe_video", fake_probe)
    tasks = _capture_tasks(monkeypatch)
    result = asyncio.run(
        main.create_video(url="https://www.bilibili.com/video/BV1New", file=None)
    )
    assert result["reused"] is False
    assert result["retry_started"] is False
    assert len(tasks) == 1


def test_qa_failure_warns_and_same_websocket_remains_usable(tmp_db, monkeypatch):
    calls = 0

    def next_qa_event(ws):
        for _ in range(5):
            event = ws.receive_json()
            if event["type"] != "progress":
                return event
        raise AssertionError("No QA event received")

    async def flaky_answer(_video_id, _position, _question, send, *, conversation_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("429 rate limited (simulated)")
        await send({"type": "qa_chunk", "text": "恢复后的回答 [00:01]"})
        await send({"type": "qa_done"})

    monkeypatch.setattr(main, "stream_answer", flaky_answer)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/v1") as ws:
            assert ws.receive_json()["type"] == "progress"

            ws.send_json({"type": "qa_request", "question": "第一次", "position": 1})
            identity = next_qa_event(ws)
            assert identity["type"] == "conversation"
            warning = next_qa_event(ws)
            assert warning["type"] == "qa_warning"
            assert "429 rate limited" in warning["message"]
            assert next_qa_event(ws)["type"] == "qa_done"

            ws.send_json({
                "type": "qa_request",
                "question": "第二次",
                "position": 1,
                "conversation_id": identity["conversation_id"],
            })
            identity_2 = next_qa_event(ws)
            assert identity_2["conversation_id"] == identity["conversation_id"]
            assert next_qa_event(ws) == {
                "type": "qa_chunk", "text": "恢复后的回答 [00:01]"
            }
            assert next_qa_event(ws)["type"] == "qa_done"


def test_ready_video_without_progress_history_sends_complete(tmp_db):
    memory.update_video_status("v1", "ready")
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws/v1") as ws:
            assert ws.receive_json() == {
                "type": "progress",
                "stage": "complete",
                "processed_up_to": 600.0,
                "total": 600.0,
                "status": "ready",
            }
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["stage"] == "complete"


def test_retry_endpoint_preserves_local_video_and_schedules_once(tmp_db, monkeypatch):
    memory.update_video_status("v1", "error", "failed")
    memory.create_conversation("v1", "保留会话")
    tasks = _capture_tasks(monkeypatch)
    with TestClient(main.app) as client:
        first = client.post("/api/videos/v1/retry")
        second = client.post("/api/videos/v1/retry")
        assert first.json() == {"video_id": "v1", "retry_started": True}
        assert second.json()["retry_started"] is False
        assert client.get("/api/videos/v1/progress").json()["progress"]["stage"] == "starting"
    assert len(tasks) == 1
    assert memory.get_video("v1")["error_message"] is None
    with memory.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations WHERE video_id = 'v1'").fetchone()[0] == 1


def test_retry_endpoint_missing_and_ready_video(tmp_db, monkeypatch):
    memory.update_video_status("v1", "ready")
    tasks = _capture_tasks(monkeypatch)
    with TestClient(main.app) as client:
        assert client.post("/api/videos/missing/retry").status_code == 404
        assert client.post("/api/videos/v1/retry").json()["retry_started"] is False
    assert not tasks
