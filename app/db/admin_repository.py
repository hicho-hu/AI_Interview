from sqlalchemy import func, or_, select

from app.db.models import Interview, JobProfile, Order, User
from app.db.repository import page_query
from app.modules.billing.service import credit_balances


def fields(row, names):
    return {name: getattr(row, name) for name in names.split()}


def mask(value):
    return (value[:1] + "**") if value else "匿名用户"


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def total(db, column, *conditions):
    return db.scalar(select(func.coalesce(func.sum(column), 0)).where(*conditions)) or 0


def listing(db, model, serialize, page=1, page_size=20, conditions=()):
    rows, size = page_query(
        db, select(model).where(*conditions).order_by(model.created_at.desc()), page, page_size
    )
    return {"items": [serialize(row) for row in rows], "total": size, "page": page, "page_size": page_size}


def user_list(db, query, status, page, page_size):
    conditions = []
    if query:
        conditions.append(
            or_(User.id.contains(query, autoescape=True), User.nickname.contains(query, autoescape=True))
        )
    if status:
        conditions.append(User.status == status)
    # Aggregate once per page, rather than running a count query for each row.
    rows, size = page_query(
        db, select(User).where(*conditions).order_by(User.created_at.desc()), page, page_size
    )
    ids = [r.id for r in rows]
    interviews = (
        dict(
            db.execute(
                select(Interview.user_id, func.count())
                .where(Interview.user_id.in_(ids))
                .group_by(Interview.user_id)
            ).all()
        )
        if ids
        else {}
    )
    payments = (
        dict(
            db.execute(
                select(Order.user_id, func.sum(Order.amount_minor))
                .where(Order.user_id.in_(ids), Order.status == "PAID")
                .group_by(Order.user_id)
            ).all()
        )
        if ids
        else {}
    )
    return {
        "items": [
            {
                **fields(r, "id status credits created_at"),
                "nickname": mask(r.nickname),
                **credit_balances(db, r),
                "interview_count": interviews.get(r.id, 0),
                "paid_minor": payments.get(r.id, 0),
            }
            for r in rows
        ],
        "total": size,
        "page": page,
        "page_size": page_size,
    }


def interview_list(db, query, status, mode, page, page_size, user_id=None):
    statement = (
        select(Interview, JobProfile.job_title, User.nickname)
        .join(JobProfile, Interview.job_profile_id == JobProfile.id)
        .join(User, Interview.user_id == User.id)
    )
    statement = statement.where(Interview.deleted_at.is_(None), User.status.notin_(["DELETED", "DELETING"]))
    if query:
        statement = statement.where(
            or_(
                Interview.id.contains(query, autoescape=True),
                Interview.user_id.contains(query, autoescape=True),
                JobProfile.job_title.contains(query, autoescape=True),
            )
        )
    for value, column in ((status, Interview.status), (mode, Interview.mode), (user_id, Interview.user_id)):
        if value:
            statement = statement.where(column == value)
    size = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(
        statement.order_by(Interview.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            {
                **fields(
                    r, "id user_id mode status current_turn_no started_at ended_at created_at failure_code"
                ),
                "job_title": title,
                "nickname": mask(nickname),
                "difficulty": r.config_json.get("difficulty", "MEDIUM"),
            }
            for r, title, nickname in rows
        ],
        "total": size,
        "page": page,
        "page_size": page_size,
    }
