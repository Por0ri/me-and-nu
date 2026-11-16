# Independent reviewer

This folder owns the separate review role. It ports the me;nu card/detail review rubric, language and safety thresholds, and page-preview expectations. It does not write or self-approve the draft.

- `prompts.py`: independent reviewer policy adapted from `ani_me;nu` to RAG evidence/chunk identifiers.
- `agent.py`: one review-queue item entry point.
- `IndependentReviewAgent.run_forever()` continuously polls the review queue and records decisions independently of collection and generation.
- The reviewer receives temporary page previews with copy overlaid for visual composition review. Stored PNG plates remain text-free. Evidence IDs, work links, and image provenance remain data-integrity requirements.
- Editorial quality is score-based: card scores contribute 70%, card-level dimensions 20%, and detail-article dimensions 10%. A computed total of 60/100 or higher passes; lower scores request revision rather than immediate rejection. One low dimension cannot independently fail the draft. The voice should feel playful, funny, specific, and fan-written; reviewers do not impose joke counts, fixed lengths, or a mandatory ending question. Image relevance scores identity, broad thematic fit, clarity, and composition; exact scene/action-to-copy matching is not required. Copyright/image-rights policy is outside the current review scope.
- Generation does not invoke this reviewer; drafts arrive through the independent review queue.
