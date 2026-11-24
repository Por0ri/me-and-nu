-- The original card image remains at position zero, including on existing cards.
CREATE TABLE IF NOT EXISTS rag_card_image_component (
    card_id TEXT NOT NULL REFERENCES rag_content_card(card_id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK(position > 0),
    asset_id TEXT NOT NULL REFERENCES rag_image_asset(asset_id),
    PRIMARY KEY(card_id, position),
    UNIQUE(card_id, asset_id)
);

CREATE VIEW IF NOT EXISTS rag_card_image_source AS
    SELECT card_id, 0 AS position, asset_id FROM rag_card_image WHERE asset_id IS NOT NULL
    UNION ALL
    SELECT card_id, position, asset_id FROM rag_card_image_component;

CREATE INDEX IF NOT EXISTS ix_rag_card_cover ON rag_content_card(card_id) WHERE page_no=1;
