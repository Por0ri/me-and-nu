# Generation and review port manifest

Reviewed and ported on 2026-11-08. `ani_me;nu` source files were read-only. No legacy module was copied wholesale or imported by `ani_rag`; role policy and visual behavior were adapted to RAG evidence/chunk IDs and the independent storage contract.

## Port decisions

| Read-only source | SHA-256 | `ani_rag` target | Decision |
|---|---|---|---|
| `../ani_me;nu/src/ani_menu_agent/prompts.py` | `EA1B9A87A59FAB5DE7FC5AD1AF6AE81A2DA70F6D29BCC07B65273DAD69583F4C` | `src/ani_rag/agents/content_generation/prompts.py` | Ported brand voice, Korean fan tone, title/card contracts, type-specific rules, and source-language handling. Replaced fact IDs with evidence/chunk IDs. |
| `../ani_me;nu/src/ani_menu_agent/generation.py` | `D40BD9B81D6222ED9AD1DDDD1DC0BE020DBD22A7E155217EC374C9DB4CF26CD1` | `src/ani_rag/model_services.py` | Ported editorial angle and distinct cover/ending strategy. Generation is limited to retrieved RAG evidence and supplied image IDs. |
| `../ani_me;nu/src/ani_menu_agent/agents/content_generation/detail_writer.py` | `B3F625026BA9216F4ED0DB562937E91FA214C6CFACD67A0001AFFF5F7AF8CA20` | `src/ani_rag/agents/content_generation/prompts.py`, `src/ani_rag/model_services.py` | Ported separate long-form article voice and evidence-only writing contract. Cards and detail article now use distinct model calls. |
| `../ani_me;nu/src/ani_menu_agent/agents/content_generation/art_director.py` | `17910F8720A1B6B4CF918B5D3BD2BFDB5A7EB09351318FCB42D3E7D7ECDA9CC7` | `src/ani_rag/agents/content_generation/layout_director.py` | Ported one-deck image-aware direction, fixed bottom HTML copy, supported emphasis/background choices, and deterministic fallback. |
| `../ani_me;nu/src/ani_menu_agent/agents/content_generation/renderer.py` | `50C32F1AAC5FEFBEC69A6682ED30A344E52416B0867546ED77B383948785EB04` | `src/ani_rag/agents/content_generation/renderer.py` (compatibility export: `src/ani_rag/rendering.py`) | Reimplemented page-by-page 1308×1632 text-free PNG plates, full-source-image contain, source-derived blurred/gradient fill, and separate normalized HTML overlay manifests. No legacy model imports. |
| `../ani_me;nu/src/ani_menu_agent/agents/content_generation/render_node.py` | `D1D658690C0EE29D345527370AF39D04CADD48C2902EA4029957F96589DFB60D` | `src/ani_rag/workers.py`, `src/ani_rag/storage/sqlite_workflow.py`, `src/ani_rag/storage/schema.sql` | Reimplemented render-before-review staging in the dedicated RAG schema. One PNG and frontend copy/layout manifest are stored per card page. |
| `../ani_me;nu/src/ani_menu_agent/agents/content_generation/graph.py` | `FF61BBABC83A226D81795E4F045D43783D730AD2E6E784963821BEEECBB4E4A4` | `src/ani_rag/runtime.py`, `src/ani_rag/agents/*/agent.py` | Ported role separation and one-item transitions without copying LangGraph, repository, or legacy state dependencies. |
| `../ani_me;nu/src/ani_menu_agent/agents/reviewer/prompt.py` | `223FAFD71AAA5C870209375DAC3747A2C3819CEA135BC866833CE9B512B71C4B` | `src/ani_rag/agents/reviewer/prompts.py`, `src/ani_rag/model_services.py` | Ported independent rubric, tone/translation checks, serious safety/evidence blockers, detail review, advisory scorecard, and optional visual preview review. |
| `../ani_me;nu/src/ani_menu_agent/agents/reviewer/README.md` | `0485311D1E2E65E89B58A50FB84D5F79DDF98BBE3454BE10E4C5F7E1386C7EA7` | `src/ani_rag/agents/reviewer/README.md` | Used as the role and decision-threshold reference. |
| `../ani_me;nu/src/ani_menu_agent/markdown_export.py` | `0BAF37D23D3AF6FB5563EAB35237EE2484FA120D6A146E8AABB95A67AF7A3093` | `src/ani_rag/content.py`, `src/ani_rag/model_services.py` | Ported the current AI notice and detail order: article, sources, then AI-use notice. |
| `../ani_me;nu/src/ani_menu_agent/models.py` | `04FB14B76B5E89E43A0125B5596942D48126D2A6CFAD358EC7879284342062E4` | `src/ani_rag/content.py`, `src/ani_rag/agents/content_generation/renderer.py` (compatibility export: `src/ani_rag/rendering.py`) | Ported per-page copy/layout fields and normalized safe-zone behavior; types use RAG evidence contracts. |

## Ownership boundaries

- `agents/mcp_crawler/` owns URL and structured MCP collection roles.
- `agents/content_generation/` owns card copy, standalone detail writing, visual art direction, PNG rendering, and frontend layout contracts.
- `agents/reviewer/` owns independent language, evidence, safety, and visual review.
- Shared models, retrieval, queues, SQLite adapters, and worker primitives remain in `ani_rag` package modules.
- Source responsibilities, factual standards, and RAG evidence schemas remain independent from the period lane.

No live generation, review, MCP request, worker, CLI, migration, or test ran during this port. At that time the MCP allowlist was empty and transport was absent. Current connection status is in [`MCP_CONNECTION_SETUP.md`](MCP_CONNECTION_SETUP.md): a bounded Firecrawl reader and project-local transport now exist, with one draft source and zero approved sources.
