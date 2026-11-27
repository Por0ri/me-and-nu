# RAG publisher routing recovery — 2026-11-27

The first mixed production run registered one RAG job, then stopped with
Celery `QueueNotFound`. The shared daily controller passed the article-only
Celery app to the RAG publisher; implicit queue creation is deliberately off.
The registered RAG queue therefore could not be resolved before broker I/O.

RAG publication now selects the existing RAG app and requires its broker URL
to match the reviewed controller broker. Neither Worker's queue scope is
expanded. Real Celery configuration with an in-memory transport reproduces
the failure and verifies a single RAG delivery, no article delivery, unchanged
article routing, and disabled/runtime/broker guards without paid calls.

After a confirmed broker response, `published_at` starts the processing wait
window. The original `reserved_at` remains unchanged. Older records or
ambiguous publish results retain reservation-based timeout behavior and are
not republished automatically. Tests cover delayed reservations, the exact
wait timeout, unchanged timestamps on continuation and ambiguous sends.

Operational evidence before recovery: original 23 records retained, one new
reserved slot, RAG job queued, zero attempt rows, no generation-start marker,
empty queue and unacked state. The daily timer remains disabled and both
owned generation Workers were stopped. The main API and recommendation
service remain healthy. No new LLM call occurred.

Recovery must retain the same job ID, admission, topic identity, daily plan,
original reservation timestamp and counted slot. Preserve an immutable
checkpoint/log/DB evidence backup before clearing only this proven
pre-broker publication intent. A broker timeout or unknown publication result
is not equivalent evidence and must not be automatically retried.

The service expiry policy deployed by PR #139 and existing pending/public
content are unaffected. Detailed operational receipts remain in the private
deployment evidence directory, without credentials or raw provider logs.
