// Recent Home searches. Kept only in this browser, per member (no server copy): another
// device or browser starts empty. Newest first, at most three; a repeat moves to the top.

/** A search the member ran: the typed words, or a suggested work (its id narrows the result). */
export type RecentSearch = { label: string; workId?: number };

export type RecentSearchStore = {
  list(): RecentSearch[];
  add(entry: RecentSearch): RecentSearch[];
  remove(entry: RecentSearch): RecentSearch[];
  clear(): RecentSearch[];
};

export const MAX_RECENT_SEARCHES = 3;
const MAX_LABEL_LENGTH = 50;
const KEY_PREFIX = "pivot.recent-searches.";

function sameSearch(a: RecentSearch, b: RecentSearch): boolean {
  return a.label === b.label && a.workId === b.workId;
}

function validEntry(value: unknown): value is RecentSearch {
  const entry = value as RecentSearch;
  return !!entry && typeof entry.label === "string" && !!entry.label.trim() && entry.label.length <= MAX_LABEL_LENGTH &&
    (entry.workId === undefined || (Number.isSafeInteger(entry.workId) && entry.workId > 0));
}

function browserStorage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; }
}

/** owner: the signed-in member id, or null (then nothing is kept). Storage errors never break search. */
export function createRecentSearchStore(owner: () => number | null, storage: () => Storage | null = browserStorage): RecentSearchStore {
  function read(): RecentSearch[] {
    const id = owner();
    if (id === null) return [];
    try {
      const saved: unknown = JSON.parse(storage()?.getItem(KEY_PREFIX + id) ?? "[]");
      return Array.isArray(saved) ? saved.filter(validEntry).map(({ label, workId }) => workId === undefined ? { label } : { label, workId }).slice(0, MAX_RECENT_SEARCHES) : [];
    } catch { return []; }
  }
  function write(entries: RecentSearch[]): RecentSearch[] {
    const id = owner();
    if (id === null) return [];
    try { storage()?.setItem(KEY_PREFIX + id, JSON.stringify(entries)); } catch { /* full or blocked storage: keep this visit's list */ }
    return entries;
  }
  return {
    list: read,
    add(entry) {
      const label = entry.label.trim();
      if (!validEntry({ ...entry, label })) return read();
      const clean = entry.workId === undefined ? { label } : { label, workId: entry.workId };
      return write([clean, ...read().filter((saved) => !sameSearch(saved, clean))].slice(0, MAX_RECENT_SEARCHES));
    },
    remove(entry) { return write(read().filter((saved) => !sameSearch(saved, entry))); },
    clear() { return write([]); },
  };
}
