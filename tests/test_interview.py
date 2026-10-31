from datetime import timedelta

from sqlalchemy import func, select

from app.core.security import utcnow
from app.db.models import AICallLog, Interview, Report, Usage
from tests.conftest import data, interview, job, login

ANSWER = "在项目中我负责接口开发与性能优化，首先定位数据库慢查询，分析执行计划，并建立复合索引。随后对热点数据设置缓存，通过压测对比 P95 延迟，同时设置监控验证改动，最终延迟从两百毫秒降到八十毫秒。"


def test_full_text_interview_and_versioned_report(client):
    headers, uid = login(client)
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    assert len(row["plan"]["questions"]) == 2
    assert "Python" in row["current_question"]["question"]
    data(client.post(path + "/start", headers=headers))
    for i in range(2):
        row = data(client.get(path, headers=headers))
        response = data(
            client.post(
                path + "/turns/" + row["current_question"]["id"] + "/submit",
                headers={**headers, "Idempotency-Key": f"answer-{i}"},
                json={"text": ANSWER},
            )
        )
        assert (
            data(client.get("/api/v1/tasks/" + response["task_id"], headers=headers))["status"] == "SUCCEEDED"
        )
    result = data(client.get(path + "/report", headers=headers))
    assert result["status"] == "COMPLETED"
    assert result["report"]["overall_score"] is None
    assert result["report"]["is_mock"]
    assert len(result["report"]["reviews"]) == 2
    assert len(result["report"]["dimensions"]) == 6
    data(client.post(path + "/report/regenerate", headers={**headers, "Idempotency-Key": "regen"}))
    assert data(client.get(path + "/report", headers=headers))["report"]["version"] == 2
    assert data(client.get(path + "/report?version=1", headers=headers))["report"]["version"] == 1
    assert data(client.get("/api/v1/interviews?status=COMPLETED", headers=headers))["total"] == 1
    with client.app.state.runtime.db.session() as db:
        assert db.scalar(select(func.count()).select_from(Report)) == 2
        assert db.scalar(select(Usage).where(Usage.usage_type == "INTERVIEW")).status == "CONSUMED"
        assert db.scalar(select(func.count()).select_from(AICallLog)) >= 6


def test_idempotency_and_quota_release(client):
    headers, uid = login(client)
    jid = job(client, headers)
    payload = {"job_profile_id": jid, "config": {"mode": "TEXT", "question_count": 1}}
    h = {**headers, "Idempotency-Key": "same"}
    first = data(client.post("/api/v1/interviews", headers=h, json=payload))
    assert data(client.post("/api/v1/interviews", headers=h, json=payload)) == first
    assert data(client.get("/api/v1/users/me", headers=headers))["remaining_interviews"] == 2
    bad = client.post(
        "/api/v1/interviews", headers=h, json={**payload, "config": {"mode": "TEXT", "question_count": 2}}
    )
    assert bad.status_code == 409
    data(client.post("/api/v1/interviews/" + first["interview_id"] + "/cancel", headers=headers))
    assert data(client.get("/api/v1/users/me", headers=headers))["remaining_interviews"] == 3


def test_pause_resume_ownership_and_double_submission(client):
    headers, uid = login(client)
    other, _ = login(client, "bob")
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    assert client.get(path, headers=other).status_code == 404
    assert client.post(path + "/pause", headers=headers).status_code == 409
    data(client.post(path + "/start", headers=headers))
    data(client.post(path + "/pause", headers=headers))
    url = path + "/turns/" + row["current_question"]["id"] + "/submit"
    assert (
        client.post(url, headers={**headers, "Idempotency-Key": "a"}, json={"text": ANSWER}).status_code
        == 409
    )
    data(client.post(path + "/resume", headers=headers))
    first = data(client.post(url, headers={**headers, "Idempotency-Key": "a"}, json={"text": ANSWER}))
    assert data(client.post(url, headers={**headers, "Idempotency-Key": "a"}, json={"text": ANSWER})) == first
    assert (
        client.post(url, headers={**headers, "Idempotency-Key": "b"}, json={"text": ANSWER}).status_code
        == 409
    )


def test_follow_up_bound_and_empty_report(client):
    headers, _ = login(client)
    row = interview(
        client, headers, config={"allow_follow_up": True, "max_follow_ups": 1, "question_count": 1}
    )
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    for i in range(2):
        row = data(client.get(path, headers=headers))
        data(
            client.post(
                path + "/turns/" + row["current_question"]["id"] + "/submit",
                headers={**headers, "Idempotency-Key": str(i)},
                json={"text": "我使用过 Python。"},
            )
        )
    assert data(client.get(path, headers=headers))["status"] == "COMPLETED"
    assert len(data(client.get(path + "/turns", headers=headers))["items"]) == 2


def test_time_limit_and_finish_idempotency(client):
    headers, _ = login(client)
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    with client.app.state.runtime.db.session() as db:
        db.get(Interview, row["id"]).started_at = utcnow() - timedelta(minutes=16)
    response = client.post(
        path + "/turns/" + row["current_question"]["id"] + "/submit",
        headers={**headers, "Idempotency-Key": "late"},
        json={"text": ANSWER},
    )
    assert response.status_code == 409
    h = {**headers, "Idempotency-Key": "finish"}
    first = data(client.post(path + "/finish", headers=h, json={"reason": "TIME_LIMIT"}))
    assert data(client.post(path + "/finish", headers=h, json={"reason": "TIME_LIMIT"})) == first
    result = data(client.get(path + "/report", headers=headers))
    assert result["status"] == "COMPLETED"
    assert result["report"]["overall_score"] is None


def test_deleting_interview_erases_history(client):
    headers, _ = login(client)
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    data(client.delete(path, headers=headers))
    assert client.get(path, headers=headers).status_code == 404
    assert data(client.get("/api/v1/interviews", headers=headers))["total"] == 0


def test_long_report_is_bounded_and_preserves_all_reviews(client):
    headers, _ = login(client)
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    for i in range(2):
        current = data(client.get(path, headers=headers))["current_question"]
        data(
            client.post(
                path + "/turns/" + current["id"] + "/submit",
                headers={**headers, "Idempotency-Key": f"long-{i}"},
                json={"text": "真实项目开发细节。" * 1600},
            )
        )
    report = data(client.get(path + "/report", headers=headers))["report"]
    assert len(report["reviews"]) == 2
    with client.app.state.runtime.db.session() as db:
        assert (
            db.scalar(select(func.count()).select_from(AICallLog).where(AICallLog.call_type == "report")) == 2
        )


def test_scheduled_timeout_finishes_without_client_action(client):
    headers, _ = login(client)
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    rt = client.app.state.runtime
    with rt.db.session() as db:
        db.get(Interview, row["id"]).started_at = utcnow() - timedelta(minutes=16)
    from app.workers.processor import expire_interviews

    expire_interviews(rt)
    rt.drain()
    assert data(client.get(path, headers=headers))["status"] == "COMPLETED"
