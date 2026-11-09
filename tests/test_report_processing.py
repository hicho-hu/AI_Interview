import json

import pytest

from app.core.errors import BusinessError
from app.providers.ai import MockProvider
from app.providers.report_evidence import prepare_report_evidence, resolve_report_evidence
from app.schemas.contracts import ReportContent
from tests.conftest import data, interview, login
from tests.test_billing_tasks import exhaust


def test_report_references_restore_verbatim_evidence_and_preserve_input():
    first = "首先分析慢查询，随后建立复合索引。\n接口 P95 降低了 40%。"
    second = "我负责测试和回滚方案。"
    payload = {
        "turns": [
            {"turn_id": "turn-one", "answer": first},
            {"turn_id": "turn-two", "answer": second},
        ]
    }
    schema = ReportContent.model_json_schema()
    request, contract, catalog = prepare_report_evidence(payload, schema)
    assert payload["turns"][0]["answer"] == first
    assert "enum" not in schema["$defs"]["Review"]["properties"]["evidence"]["items"]
    assert contract["$defs"]["Review"]["properties"]["evidence"]["items"]["enum"] == list(catalog)
    first_id = request["turns"][0]["answer_segments"][0]["evidence_id"]
    second_id = request["turns"][1]["answer_segments"][0]["evidence_id"]
    resolved = resolve_report_evidence(
        {
            "reviews": [{"turn_id": "turn-one", "evidence": [first_id]}],
            "dimensions": [{"name": "问题解决", "evidence": [first_id, second_id]}],
        },
        catalog,
    )
    assert resolved["reviews"][0]["evidence"] == ["首先分析慢查询，随后建立复合索引。"]
    assert resolved["dimensions"][0]["evidence"][0] in first
    assert resolved["dimensions"][0]["evidence"][1] in second


@pytest.mark.parametrize(
    "reference,turn_id",
    [
        ("改写后的原文", "turn-one"),
        ("missing-reference", "turn-one"),
        ("T2E1", "turn-one"),
    ],
)
def test_report_rejects_invented_or_cross_answer_evidence(reference, turn_id):
    _, _, catalog = prepare_report_evidence(
        {
            "turns": [
                {"turn_id": "turn-one", "answer": "原文中的行动。"},
                {"turn_id": "turn-two", "answer": "另一份回答的行动。"},
            ]
        },
        ReportContent.model_json_schema(),
    )
    with pytest.raises(BusinessError) as error:
        resolve_report_evidence(
            {"reviews": [{"turn_id": turn_id, "evidence": [reference]}], "dimensions": []}, catalog
        )
    assert error.value.code == "AI_OUTPUT_INVALID"


def test_report_without_answers_cannot_invent_evidence():
    request, contract, catalog = prepare_report_evidence({"turns": []}, ReportContent.model_json_schema())
    assert request["turns"] == []
    assert contract["$defs"]["Dimension"]["properties"]["evidence"]["maxItems"] == 0
    with pytest.raises(BusinessError):
        resolve_report_evidence({"reviews": [], "dimensions": [{"evidence": ["invented"]}]}, catalog)


@pytest.mark.parametrize("scores", [[72, 65, 58, 60, 55, 70], [72, None, None, None, None, 70]])
def test_report_preserves_supported_scores_and_partial_assessment(client, scores):
    headers, _ = login(client)
    row = interview(client, headers, config={"question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))

    class ScoredReport(MockProvider):
        def invoke(self, operation, payload, schema):
            result = super().invoke(operation, payload, schema)
            if operation == "report":
                report = result["data"]
                for dimension, score in zip(report["dimensions"], scores):
                    if score is not None:
                        dimension.update(
                            score=score,
                            feedback="已说明具体行动，但量化结果与技术权衡仍需补充。",
                            evidence=[payload["turns"][0]["answer"]],
                        )
                report["overall_score"] = 0  # Model arithmetic must not discard supported dimension scores.
            return result

    client.app.state.runtime.provider = ScoredReport()
    answer = "我用执行计划定位慢查询，再建立复合索引；量化结果还没整理完。"
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "answer"},
            json={"text": answer},
        )
    )
    for version in (1, 2):
        if version == 2:
            data(client.post(path + "/report/regenerate", headers={**headers, "Idempotency-Key": "regen"}))
        result = data(client.get(path + "/report", headers=headers))
        assert result["status"] == "COMPLETED"
        report = result["report"]
        assert report["version"] == version
        assert [dimension["score"] for dimension in report["dimensions"]] == scores
        assessed = [score for score in scores if score is not None]
        assert report["overall_score"] == round(sum(assessed) / len(assessed), 1)
        for dimension in report["dimensions"]:
            assert dimension["evidence"] == ([answer] if dimension["score"] is not None else [])
    assert data(client.get(path + "/report?version=1", headers=headers))["report"]["version"] == 1


def test_report_rejects_unassessed_dimensions_with_assessment_evidence(client):
    headers, _ = login(client)
    row = interview(client, headers, config={"question_count": 1})
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))

    class ContradictoryReport(MockProvider):
        def invoke(self, operation, payload, schema):
            result = super().invoke(operation, payload, schema)
            if operation == "report":
                for dimension in result["data"]["dimensions"]:
                    dimension["evidence"] = [payload["turns"][0]["answer"]]
                    dimension["feedback"] = "无法判断：已介绍具体行动，但案例未讲完。"
            return result

    client.app.state.runtime.provider = ContradictoryReport()
    data(
        client.post(
            path + "/turns/" + row["current_question"]["id"] + "/submit",
            headers={**headers, "Idempotency-Key": "answer"},
            json={"text": "我先识别重复下发和消息丢失风险，再验证幂等、对账和失败重试。"},
        )
    )
    result = data(client.get(path + "/report", headers=headers))
    exhaust(client.app.state.runtime, result["task_id"])
    result = data(client.get(path + "/report", headers=headers))
    assert result["status"] == "REPORT_FAILED"
    assert result["failure_code"] == "AI_OUTPUT_INVALID"
    assert result["report"] is None


def test_report_retries_once_per_task_attempt_and_exposes_failure(client):
    headers, _ = login(client)
    other, _ = login(client, "another-owner")
    row = interview(client, headers)
    path = "/api/v1/interviews/" + row["id"]
    data(client.post(path + "/start", headers=headers))
    rt = client.app.state.runtime

    class InvalidReport(MockProvider):
        report_calls = 0

        def invoke(self, operation, payload, schema):
            if operation == "report":
                self.report_calls += 1
                raise BusinessError("AI_OUTPUT_INVALID", "逐题证据不在回答中", 502)
            return super().invoke(operation, payload, schema)

    provider = InvalidReport()
    rt.provider, rt.settings.provider_retry_count = provider, 2
    task = data(client.post(path + "/finish", headers={**headers, "Idempotency-Key": "finish"}, json={}))
    progress = data(client.get(path + "/report", headers=headers))
    assert progress["status"] == "FINISHING"
    assert progress["processing_status"] == "RETRYING"
    assert progress["retry_count"] == 1
    assert progress["last_error_code"] == "AI_OUTPUT_INVALID"
    assert progress["retryable"] is False
    exhaust(rt, task["task_id"])
    failed = data(client.get(path + "/report", headers=headers))
    assert failed["status"] == "REPORT_FAILED"
    assert failed["processing_status"] == "FAILED"
    assert failed["failure_code"] == "AI_OUTPUT_INVALID"
    assert failed["retryable"] is True
    assert provider.report_calls == 3
    assert client.get(path + "/report", headers=other).status_code == 404
    rt.provider = MockProvider()
    data(client.post(path + "/retry", headers=headers))
    completed = data(client.get(path + "/report", headers=headers))
    assert completed["status"] == "COMPLETED"
    assert completed["processing_status"] == "SUCCEEDED"
    assert completed["failure_code"] is None
    assert completed["retryable"] is False


def test_report_validation_logs_exclude_model_content(client, caplog, monkeypatch):
    from app.schemas.contracts import JobStructure

    headers, uid = login(client)
    rt = client.app.state.runtime
    # Alembic's test migration config disables pre-existing named loggers.
    monkeypatch.setattr("app.runtime.logger.disabled", False)

    class InvalidOutput(MockProvider):
        def invoke(self, operation, payload, schema):
            return {"data": {"private-upstream-field": "private-answer-text"}, "usage": {}}

    rt.provider = InvalidOutput()
    with rt.db.sessions() as db:
        with pytest.raises(BusinessError):
            rt.ai(db, uid, "job", {"jd_text": "private-answer-text"}, JobStructure)
        db.rollback()
    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.message.startswith('{"event": "ai_output_invalid"')
    ]
    assert events and events[0]["validation_errors"]
    assert "private-upstream-field" not in caplog.text
    assert "private-answer-text" not in caplog.text
