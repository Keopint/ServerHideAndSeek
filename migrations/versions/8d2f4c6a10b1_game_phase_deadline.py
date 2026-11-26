"""Persist the current game phase deadline.

Revision ID: 8d2f4c6a10b1
Revises: 5874e928d46e
"""
from alembic import op

revision = '8d2f4c6a10b1'
down_revision = '5874e928d46e'
branch_labels = None
depends_on = None


def upgrade():
    # Идемпотентно: колонка могла быть добавлена при запуске сервера.
    op.execute('ALTER TABLE games ADD COLUMN IF NOT EXISTS phase_ends_at TIMESTAMPTZ')


def downgrade():
    op.execute('ALTER TABLE games DROP COLUMN IF EXISTS phase_ends_at')
