-- Read-only export helper for the authoritative topic/subtopic catalog.
-- Run only against the approved application database when its owner authorizes
-- exporting these catalog rows. This file is not a migration and was not run.

SELECT
    topic_id,
    topic_code,
    topic_name,
    is_active
FROM topic
ORDER BY topic_id;

SELECT
    subtopic_id,
    topic_id,
    parent_subtopic_id,
    subtopic_name,
    depth_level
FROM subtopic
ORDER BY topic_id, subtopic_id;
