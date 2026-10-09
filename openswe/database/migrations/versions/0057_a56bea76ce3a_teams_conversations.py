"""Teams conversations: which thread each direct message with the bot continues"""

from alembic import op

revision = "a56bea76ce3a"
down_revision = "30ff41a80071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A conversation's thread id derives from (conversation_id, generation), so
    # starting over is a bump of the generation and nothing else is stored.
    op.execute(
        """
        CREATE TABLE teams_conversation (
            conversation_id text PRIMARY KEY,
            generation integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError
