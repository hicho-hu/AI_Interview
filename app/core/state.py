from app.core.errors import require

TRANSITIONS = {
    "DRAFT": {"PREPARING", "CANCELLED"},
    "PREPARING": {"READY", "FAILED", "CANCELLED"},
    "READY": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"PAUSED", "FINISHING", "CANCELLED"},
    "PAUSED": {"IN_PROGRESS", "FINISHING", "CANCELLED"},
    "FAILED": {"PREPARING", "CANCELLED"},
    "FINISHING": {"COMPLETED", "REPORT_FAILED"},
    "REPORT_FAILED": {"FINISHING", "CANCELLED"},
    "COMPLETED": {"FINISHING"},
    "CANCELLED": set(),
}


def transition(interview, target: str):
    require(
        target in TRANSITIONS.get(interview.status, set()),
        "INTERVIEW_INVALID_STATE",
        f"当前状态 {interview.status} 不允许变为 {target}",
        409,
    )
    interview.status = target
