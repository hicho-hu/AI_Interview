"""Run against a local development server: python scripts/smoke.py [base_url]."""

import sys
import time
import uuid

import httpx

base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


def main():
    with httpx.Client(base_url=base, timeout=60) as client:

        def call(method, path, body=None, key=None):
            headers = {"Idempotency-Key": key} if key else {}
            response = client.request(method, "/api/v1" + path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()["data"]

        auth = call("POST", "/auth/dev-login", {"identity": "smoke-" + uuid.uuid4().hex})
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        for kind in ("PRIVACY", "TERMS", "AI_PROCESSING"):
            call("POST", "/users/me/consents", {"consent_type": kind, "policy_version": "1.0"})
        job = call(
            "POST",
            "/job-profiles",
            {"job_title": "Python 后端工程师", "jd_text": "负责 Python API 开发、SQL 数据库设计与性能优化"},
        )
        wait_task(call, job["task_id"])
        created = call(
            "POST",
            "/interviews",
            {
                "job_profile_id": job["id"],
                "config": {"mode": "TEXT", "question_count": 1, "allow_follow_up": False},
            },
            "smoke-create",
        )
        wait_task(call, created["task_id"])
        path = "/interviews/" + created["interview_id"]
        row = call("POST", path + "/start")
        submitted = call(
            "POST",
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            {
                "text": "我负责设计接口和数据库，在真实项目中通过慢查询分析和索引优化改善性能。"
                "我先分析执行计划，比较多种实现方式，再压测确认改动效果，并持续监控错误率。"
            },
            "smoke-answer",
        )
        wait_task(call, submitted["task_id"])
        row = call("GET", path)
        wait_task(call, row["report_task_id"])
        result = call("GET", path + "/report")
        assert result["status"] == "COMPLETED"
        print(
            f"PASS: interview={created['interview_id']} report_version={result['report']['version']} "
            f"mock={result['report']['is_mock']}"
        )
        call("DELETE", path)
        call("DELETE", "/job-profiles/" + job["id"])
        call("DELETE", "/users/me/data")


def wait_task(call, task_id):
    for _ in range(120):
        task = call("GET", "/tasks/" + task_id)
        if task["status"] == "SUCCEEDED":
            return
        if task["status"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"Task failed: {task['error_code']}")
        time.sleep(0.5)
    raise TimeoutError(task_id)


if __name__ == "__main__":
    main()
