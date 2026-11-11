# Independent reviewer

This folder owns the separate review role. It ports the me;nu card/detail review rubric, language and safety thresholds, and page-preview expectations. It does not write or self-approve the draft.

- `prompts.py`: independent reviewer policy adapted from `ani_me;nu` to RAG evidence/chunk identifiers.
- `agent.py`: one review-queue item entry point.
- `IndependentReviewAgent.run_forever()` continuously polls the review queue and records decisions independently of collection and generation.
- The review worker runs deterministic evidence, ID, image-rights, and layout checks first. The reviewer receives temporary page previews with copy overlaid for visual composition review. Stored PNG plates remain text-free.
- The current pilot checks carousel order and copy readability. Copyright, image rights, and factual-support scores are not pass blockers. Generation does not invoke this reviewer; drafts arrive through the independent review queue.
