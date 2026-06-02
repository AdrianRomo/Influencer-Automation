"""Add catalog-to-ad B2B layer.

Creates the multi-tenant catalog tables (workspaces, workspace_members, brands,
catalogs, products, product_images, campaigns, ad_concepts) and adds nullable
workspace_id/product_id/ad_concept_id linkage columns to the existing asset
tables so the same render pipeline serves both article- and catalog-sourced jobs.

Fully additive: existing article→video flow is untouched. A separate data
migration (later) backfills a personal workspace per existing user.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def _tables(conn) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _cols(conn, table: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1] for r in conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()}
    return {
        r[0] for r in conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=:t"),
            {"t": table},
        ).fetchall()
    }


# Asset tables that gain catalog linkage columns. usage events also get product_id.
_ASSET_TABLES = ("audio_assets", "image_assets", "video_assets", "generation_usage_events")
_LINK_COLS = ("workspace_id", "product_id", "ad_concept_id")


def upgrade() -> None:
    conn = op.get_bind()
    existing = _tables(conn)

    if "workspaces" not in existing:
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("owner_user_id", sa.String(), nullable=False),
            sa.Column("plan", sa.String(length=50), nullable=False, server_default="beta"),
            sa.Column("credits_balance", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_workspaces_owner_user_id", "workspaces", ["owner_user_id"])

    if "workspace_members" not in existing:
        op.create_table(
            "workspace_members",
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("workspace_id", "user_id"),
        )

    if "brands" not in existing:
        op.create_table(
            "brands",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("site_url", sa.String(), nullable=True),
            sa.Column("tone_json", sa.JSON(), nullable=True),
            sa.Column("target_audience_json", sa.JSON(), nullable=True),
            sa.Column("brand_voice_json", sa.JSON(), nullable=True),
            sa.Column("prohibited_words", sa.JSON(), nullable=True),
            sa.Column("claim_policy_json", sa.JSON(), nullable=True),
            sa.Column("ai_disclosure", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("default_language", sa.String(length=20), nullable=False, server_default="en"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_brands_workspace_id", "brands", ["workspace_id"])

    if "catalogs" not in existing:
        op.create_table(
            "catalogs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("brand_id", sa.String(), nullable=True),
            sa.Column("source_type", sa.String(length=30), nullable=False),
            sa.Column("source_ref", sa.String(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_catalogs_workspace_id", "catalogs", ["workspace_id"])

    if "products" not in existing:
        op.create_table(
            "products",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("brand_id", sa.String(), nullable=True),
            sa.Column("catalog_id", sa.String(), nullable=True),
            sa.Column("external_id", sa.String(), nullable=True),
            sa.Column("url", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(length=10), nullable=True),
            sa.Column("category", sa.String(), nullable=True),
            sa.Column("attributes_json", sa.JSON(), nullable=True),
            sa.Column("primary_image_url", sa.String(), nullable=True),
            sa.Column("analysis_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["catalog_id"], ["catalogs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("catalog_id", "external_id", name="uq_product_catalog_extid"),
        )
        op.create_index("ix_products_workspace_id", "products", ["workspace_id"])
        op.create_index("ix_products_catalog_id", "products", ["catalog_id"])
        op.create_index("ix_products_workspace_created", "products", ["workspace_id", "created_at"])

    if "product_images" not in existing:
        op.create_table(
            "product_images",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("product_id", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=True),
            sa.Column("file_path", sa.String(), nullable=True),
            sa.Column("is_primary", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_product_images_product_id", "product_images", ["product_id"])

    if "campaigns" not in existing:
        op.create_table(
            "campaigns",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("brand_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("goal", sa.String(length=30), nullable=False, server_default="awareness"),
            sa.Column("platforms", sa.JSON(), nullable=True),
            sa.Column("product_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["brand_id"], ["brands.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_campaigns_workspace_id", "campaigns", ["workspace_id"])

    if "ad_concepts" not in existing:
        op.create_table(
            "ad_concepts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.String(), nullable=False),
            sa.Column("campaign_id", sa.String(), nullable=True),
            sa.Column("product_id", sa.String(), nullable=False),
            sa.Column("variant_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("angle", sa.String(), nullable=True),
            sa.Column("hook", sa.Text(), nullable=True),
            sa.Column("headline", sa.Text(), nullable=True),
            sa.Column("script_json", sa.JSON(), nullable=True),
            sa.Column("captions_json", sa.JSON(), nullable=True),
            sa.Column("cta", sa.Text(), nullable=True),
            sa.Column("on_screen_text", sa.Text(), nullable=True),
            sa.Column("storyboard_json", sa.JSON(), nullable=True),
            sa.Column("compliance_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ad_concepts_workspace_id", "ad_concepts", ["workspace_id"])
        op.create_index("ix_ad_concepts_product_variant", "ad_concepts", ["product_id", "variant_index"])
        op.create_index("ix_ad_concepts_campaign", "ad_concepts", ["campaign_id"])

    # Additive linkage columns on existing asset tables.
    for table in _ASSET_TABLES:
        if table not in existing:
            continue
        present = _cols(conn, table)
        with op.batch_alter_table(table) as batch_op:
            for col in _LINK_COLS:
                if col not in present:
                    batch_op.add_column(sa.Column(col, sa.String(), nullable=True))
        for col in _LINK_COLS:
            if col not in present:
                op.create_index(f"ix_{table}_{col}", table, [col])


def downgrade() -> None:
    conn = op.get_bind()
    existing = _tables(conn)

    for table in _ASSET_TABLES:
        if table not in existing:
            continue
        present = _cols(conn, table)
        for col in _LINK_COLS:
            if col in present:
                try:
                    op.drop_index(f"ix_{table}_{col}", table_name=table)
                except Exception:
                    pass
        with op.batch_alter_table(table) as batch_op:
            for col in _LINK_COLS:
                if col in present:
                    batch_op.drop_column(col)

    for table in (
        "ad_concepts", "campaigns", "product_images", "products",
        "catalogs", "brands", "workspace_members", "workspaces",
    ):
        if table in existing:
            op.drop_table(table)
