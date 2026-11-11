# Content generation agent

This folder owns card copy, a separate “자세히 보기” article, art direction, PNG rendering, and the frontend HTML overlay contract. It follows the me;nu fan voice and editorial structure while taking only retrieved RAG evidence and chunks as factual support.

- `prompts.py`: ported brand voice, card copy rules, separate detail-article rules, and image/HTML layout policy.
- `layout_director.py`: one vision decision per page compares the selected artwork with the actual card copy before choosing emphasis/background; an uncertain image-to-copy match blocks the draft.
- `agent.py`: one generation-queue item entry point.
- `ContentGenerationAgent.run_forever()` repeatedly reconciles stored evidence backlog rows into generation jobs, consumes jobs, saves drafts, and queues completed drafts for review. It runs only when explicitly started by a host process.
- `ani_rag.agents.content_generation.renderer` (compatibility export: `ani_rag.rendering`): creates one text-free PNG per page and returns separate copy/layout manifests; the generation worker persists each page PNG with its card record.

The PNG is never flattened with text. The frontend receives normalized page-specific `image_box` and `text_overlay_box` coordinates plus card copy. Image plates preserve the full source artwork, use its same-image blurred extension with the me;nu source-derived palette blend, and feather the sharp artwork into that background. Generation requires at least three matched source images and every card must use a distinct registered image; there is no text-only image fallback. Detail content is returned as article text, source section, and the established AI notice in that order.
