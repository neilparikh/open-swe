"""Microsoft identities: people link the Entra account Teams knows them by"""

from alembic import op

revision = "30ff41a80071"
down_revision = ["4ce55eec2786", "42e9e3af43e3", "b58096f6b755", "f11526182620"]
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_identity DROP CONSTRAINT user_identity_provider_check")
    op.execute(
        "ALTER TABLE user_identity ADD CONSTRAINT user_identity_provider_check "
        "CHECK (provider IN ('github', 'slack', 'microsoft'))"
    )


def downgrade() -> None:
    raise NotImplementedError
