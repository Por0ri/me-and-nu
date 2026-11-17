// Development review data is deliberately separate from Policy/ConsentInput.
export type ReviewPolicyId = "terms" | "operations" | "privacy";
export type ReviewBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: { number?: number; text: string; children: ReviewBlock[] }[] }
  | { kind: "table"; header: string[]; rows: string[][] };
export type ReviewDocument = { id: ReviewPolicyId; title: string; version: string; effectiveDate: string; blocks: ReviewBlock[] };

export function isLoopbackPolicyUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) && !url.username && !url.password;
  } catch { return false; }
}

export function isLocalPolicyReviewUrl(value: string): boolean {
  if (!isLoopbackPolicyUrl(value)) return false;
  const url = new URL(value);
  return url.pathname === "/pivot/demo" && url.searchParams.get("policyReview") === "1";
}

/** The source uses headings, paragraphs, nested lists and pipe tables only.
 * Render literal strings, never HTML or automatic links. Preserve list numbers
 * across intervening tables (e.g. the operations sanctions clauses).
 */
export function parseReviewBlocks(markdown: string): ReviewBlock[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const listItem = (line: string) => /^( *)(?:(\d+)\.|(-)) (.+)$/.exec(line);
  let cursor = 0;
  function readList(indent: number, ordered: boolean): ReviewBlock {
    const items: Extract<ReviewBlock, { kind: "list" }>["items"] = [];
    while (cursor < lines.length) {
      const match = listItem(lines[cursor]);
      if (!match || match[1].length !== indent || Boolean(match[2]) !== ordered) break;
      cursor++;
      const item = { number: ordered ? Number(match[2]) : undefined, text: match[4], children: [] as ReviewBlock[] };
      while (cursor < lines.length) {
        const nested = listItem(lines[cursor]);
        if (!nested || nested[1].length <= indent) break;
        item.children.push(readList(nested[1].length, Boolean(nested[2])));
      }
      items.push(item);
    }
    return { kind: "list", ordered, items };
  }
  const blocks: ReviewBlock[] = [];
  while (cursor < lines.length) {
    const line = lines[cursor];
    if (!line.trim() || line.trim() === "---") { cursor++; continue; }
    const heading = /^(#{3,4}) (.+)$/.exec(line);
    if (heading) { blocks.push({ kind: "heading", level: heading[1].length + 1, text: heading[2] }); cursor++; continue; }
    const list = listItem(line);
    if (list) { blocks.push(readList(list[1].length, Boolean(list[2]))); continue; }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (cursor < lines.length && lines[cursor].startsWith("|")) {
        const row = lines[cursor++].trim().slice(1, -1).split("|").map((cell) => cell.trim());
        if (!row.every((cell) => /^:?-+:?$/.test(cell))) rows.push(row);
      }
      const [header, ...body] = rows;
      if (!header?.length || body.some((row) => row.length !== header.length)) throw new Error("Invalid policy table");
      blocks.push({ kind: "table", header, rows: body }); continue;
    }
    const paragraph = [line]; cursor++;
    while (cursor < lines.length && lines[cursor].trim() && !/^(?:#|\||---)/.test(lines[cursor]) && !listItem(lines[cursor])) paragraph.push(lines[cursor++]);
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }
  return blocks;
}

export function parseTeamPolicyReview(value: unknown): ReviewDocument[] {
  if (typeof value !== "string") throw new Error("Missing review source");
  const sections = value.replace(/\r\n/g, "\n").split(/^## \d{2} /m).slice(1);
  const expected = [
    { id: "terms", title: "이용약관", version: "v1.2" },
    { id: "operations", title: "운영정책", version: "v0.6" },
    { id: "privacy", title: "개인정보처리방침", version: "v0.3" },
  ] as const;
  if (sections.length !== expected.length) throw new Error("Incomplete review source");
  return expected.map((document, index) => {
    const match = /^([^\n]+)\n\n버전 (\S+) · 시행일 (\d{4})년 (\d{1,2})월 (\d{1,2})일\n\n([\s\S]+)$/.exec(sections[index]);
    if (!match || match[1] !== document.title || match[2] !== document.version) throw new Error("Unexpected review document");
    const effectiveDate = `${match[3]}-${match[4].padStart(2, "0")}-${match[5].padStart(2, "0")}`;
    if (effectiveDate !== "2026-11-02") throw new Error("Unexpected review effective date");
    const blocks = parseReviewBlocks(match[6]);
    if (!blocks.length) throw new Error("Empty review document");
    return { ...document, effectiveDate, blocks };
  });
}
