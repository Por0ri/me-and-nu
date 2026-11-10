# Backend integration: monthly character birthday content

## Purpose

This note describes how the agent stores character birthday references and how a service can use them to select birthday content relevant to each user's followed works.

## Storage contract

No database table or column was added. Both record types use the existing `agent_records` table and its `payload_json` field.

| `record_kind` | Purpose | Expiration | Backend use |
|---|---|---|---|
| `character_birthday_reference` | Reusable birthday reference from Onnada | None | Reference catalog; filter by month and followed work IDs |
| `candidate_attempt` | Internal record that a discovery candidate was processed | None | Agent deduplication only; exclude from user-facing feeds |

### `character_birthday_reference` payload

Each reference contains these fields:

| Field | Meaning |
|---|---|
| `birthday_mmdd` | Birthday as `MM-DD`; it is not a publication date or a year-specific event date |
| `character_name` | Character name from the Onnada listing |
| `linked_work_title` | Work title linked to the character on Onnada |
| `linked_work_ids` | Work IDs resolved from the work title by the agent catalog |
| `menu_work_ids` | Same resolved IDs in the field name used by feed/content packages |
| `work_match_status` | `matched` when at least one ID is resolved; otherwise `unresolved` |
| `url` | Onnada character profile URL |
| `source_id` | `onnada` |
| `summary` | Provenance note and extracted birthday/work details |

Unresolved records remain in the reference catalog for later matching, but they have no `menu_work_ids` and must not be selected for a personalized feed.

The Onnada profile is the reference URL stored here. When producing an article, the agent should capture the profile and any linked source used for supporting details; the birthday reference alone is not a generated content item.

### `candidate_attempt` payload

This is an internal deduplication record keyed by content type and stable candidate ID. It stores the latest attempt status and has no expiry. It is not feed content and should not be included in user-facing content queries.

## Monthly collection behavior

- The character crawler requests the current month using Korea Standard Time (`Asia/Seoul`).
- The birthday itself is stored as recurring month/day (`MM-DD`), so the catalog entry remains useful in later years.
- Monthly references are upserted by stable candidate identity. Re-running collection updates the same record rather than creating a new copy.
- Regular discovery candidates have a separate lifecycle and may expire after 30 days. This does not expire the birthday reference or the candidate-attempt history.

## Feed selection

For a user with followed works, use the current month and intersect the user's work IDs with the reference's `menu_work_ids`:

```text
month = current month in Asia/Seoul
followed_work_ids = IDs followed by this user

eligible birthdays = references where:
  birthday_mmdd starts with zero-padded month + "-"
  AND menu_work_ids intersects followed_work_ids
```

If the user has no followed works, or no current-month birthday matches those works, return no birthday item. Do not fill the feed with unrelated characters. Sort eligible references by `birthday_mmdd` and then use the normal feed ranking rules.

The storage helper `AgentRepository.list_character_birthday_references(month=..., menu_work_ids=...)` applies this filter. The feed should still display generated, approved content from the normal feed/content pipeline; a raw reference row is not publishable content.

## Database and deployment status

The agent workspace currently selects local SQLite at:

```text
data/ani_menu_local.sqlite3
```

The backend's production PostgreSQL does not automatically see rows in this local file. Before the service consumes birthday references, the team must choose one of these deployment paths:

1. Configure the agent to write to the same PostgreSQL database used by the service; or
2. Add a controlled sync/export step from the agent database to the service database.

No schema migration is needed for this integration. The birthday references are written to existing `agent_records` rows. Monthly runs upsert the current month's reference records; previously saved month records remain available.

Initial local catalog snapshot (2026-11-02): November has 366 birthday references. The agent catalog resolved 155 references to work IDs; 211 references across 150 distinct work titles remain unresolved. Unresolved references are retained for catalog matching and are excluded from personalized results until their work IDs are resolved.

## Backend checklist

1. Keep user followed-work IDs in the same `menu_work_id` namespace as agent content.
2. Read only `character_birthday_reference` when building the birthday reference set; ignore `candidate_attempt`.
3. Filter by the Korea Standard Time month and intersect `menu_work_ids` with the user's followed works.
4. Display only approved/generated content through the existing feed pipeline.
5. Confirm how local agent SQLite data reaches production PostgreSQL before enabling the feature.
