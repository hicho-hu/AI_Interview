"""MySQL storage baseline: complete cyclic foreign keys after all tables exist.

Revision ID: f4c8a1d2e903
Revises: 867baba5ca32
"""

from alembic import op

revision = "f4c8a1d2e903"
down_revision = "867baba5ca32"
branch_labels = None
depends_on = None

CONSTRAINTS = (
    ("fk_interview_resume", "interviews", "resume_assets", "resume_asset_id"),
    ("fk_turn_answer", "interview_turns", "media_assets", "answer_asset_id"),
    ("fk_turn_tts", "interview_turns", "media_assets", "question_asset_id"),
    ("fk_media_turn", "media_assets", "interview_turns", "turn_id"),
)


def upgrade():
    if op.get_bind().dialect.name == "mysql":
        for name, source, target, column in CONSTRAINTS:
            op.create_foreign_key(name, source, target, [column], ["id"])


def downgrade():
    if op.get_bind().dialect.name == "mysql":
        for name, source, _, _ in reversed(CONSTRAINTS):
            op.drop_constraint(name, source, type_="foreignkey")
