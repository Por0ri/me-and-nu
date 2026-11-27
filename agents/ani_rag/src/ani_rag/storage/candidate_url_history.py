"""Locate older SQLite candidate keys without rewriting stored articles or taking over live leases."""

from __future__ import annotations

import hashlib
import sqlite3

from ani_rag.adapter import canonical_source_item_url, canonical_web_candidate_url


class CandidateURLHistory:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._legacy: dict[str, set[str]] | None = None

    def _aliases(self, article: str) -> set[str]:
        # Only an old worker can write keys in the previous format. Index them once,
        # lazily after schema initialization. All new jobs use the new canonical key.
        if self._legacy is None:
            legacy: dict[str, set[str]] = {}
            for row in self.connection.execute(
                """SELECT url FROM (
                    SELECT canonical_url AS url FROM rag_web_candidate_url_ledger
                    UNION ALL SELECT canonical_url FROM rag_web_candidate_page_cache
                    UNION ALL SELECT json_extract(payload_json,'$.candidate.url') FROM rag_queue_job
                        WHERE handler_kind='web_candidate_assessment'
                ) WHERE url LIKE '%srsltid=%' OR url LIKE '%blog.naver.com/%'
                   OR (url LIKE '%wikipedia.org%/wiki/%' AND (instr(url,'%28')>0 OR instr(url,'%29')>0))"""
            ):
                try:
                    old = canonical_source_item_url(row[0])
                    new = canonical_web_candidate_url(old)
                except ValueError:
                    continue
                if old != new:
                    legacy.setdefault(new, set()).add(old)
            self._legacy = legacy
        return self._legacy.get(article, set())

    def assessment_prefixes(self, article: str, prefix: str) -> list[str]:
        stem = prefix.rpartition(":")[0]
        return [prefix, *(f"{stem}:{hashlib.sha256(old.encode()).hexdigest()[:24]}"
                          for old in sorted(self._aliases(article)))]

    def storage_key(self, article: str, now_text: str) -> str:
        aliases = self._aliases(article)
        if not aliases:
            return article
        urls = (article, *sorted(aliases))
        marks = ",".join("?" for _ in urls)
        cached = self.connection.execute(
            f"""SELECT canonical_url FROM rag_web_candidate_page_cache
                WHERE canonical_url IN ({marks}) AND (
                    (status='fetching' AND lease_expires_at>?) OR (status='ready' AND expires_at>?)
                    OR (status='failed' AND (retry_after>? OR last_error_code='unsupported_target')))
                ORDER BY CASE status WHEN 'fetching' THEN 1 WHEN 'ready' THEN 2 ELSE 3 END,
                         updated_at DESC, canonical_url LIMIT 1""",
            (*urls, now_text, now_text, now_text),
        ).fetchone()
        if cached:
            return str(cached[0])
        ledger = self.connection.execute(
            f"""SELECT canonical_url FROM rag_web_candidate_url_ledger WHERE canonical_url IN ({marks})
                ORDER BY updated_at DESC, canonical_url LIMIT 1""", urls,
        ).fetchone()
        return str(ledger[0]) if ledger else article
