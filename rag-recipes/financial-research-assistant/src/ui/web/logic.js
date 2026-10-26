// Pure helpers for the web app (no DOM, no network) so they can be unit tested with `node --test`.

const DOC_NAMES = {
  bank_of_america_2024: "Bank of America 2024",
  morgan_stanley_2024: "Morgan Stanley 2024",
  wells_fargo_2024: "Wells Fargo 2024",
};

/** Friendly document name from an id such as bank_of_america_2024. */
export function docName(docId) {
  if (DOC_NAMES[docId]) return DOC_NAMES[docId];
  return String(docId).split("_").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

// [doc_id p.58]  or  [doc_id p.58, p.59]  or  [doc_id p.58, 59]  or, with where on the page the fact is, [doc_id p.58 | table]
const CITE_RE = /\[([a-z0-9_]+)\s+p\.(\d+(?:\s*,\s*(?:p\.)?\d+)*)(?:\s*\|\s*([A-Za-z ]{1,20}))?\]/g;

/** Where on a page a cited fact is, as the assistant states it. Anything else it writes is ignored. */
export const PAGE_KINDS = {
  table: "Table",
  chart: "Chart",
  text: "Text",
  footnote: "Footnote",
  figure: "Figure",
};

function cleanKind(raw) {
  const kind = String(raw || "").trim().toLowerCase();
  return Object.hasOwn(PAGE_KINDS, kind) ? kind : null;
}

/**
 * Splits text into plain and citation segments.
 * Returns [{type:"text", text} | {type:"cite", doc, page, label, kind}] with one cite segment per cited page.
 * kind is one of the PAGE_KINDS keys, or null when the assistant gave none (or something else).
 */
export function parseCitations(text) {
  const out = [];
  let last = 0;
  const source = String(text);
  for (const m of source.matchAll(CITE_RE)) {
    if (m.index > last) out.push({ type: "text", text: source.slice(last, m.index) });
    const pages = m[2].split(",").map((p) => Number(p.replace(/[^0-9]/g, "")));
    const kind = cleanKind(m[3]);
    pages.forEach((page, i) => {
      if (i > 0) out.push({ type: "text", text: " " });
      out.push({ type: "cite", doc: m[1], page, label: `${m[1]} p.${page}`, kind });
    });
    last = m.index + m[0].length;
  }
  if (last < source.length) out.push({ type: "text", text: source.slice(last) });
  return out;
}

/** Set of "doc p.N" labels cited anywhere in the text. */
export function citedLabels(text) {
  return new Set(parseCitations(text).filter((s) => s.type === "cite").map((s) => s.label));
}

/** Map of "doc p.N" label to the kinds ("table", "chart", ...) the answer cites that page for, in order of first use. */
export function citedKinds(text) {
  const out = new Map();
  for (const seg of parseCitations(text)) {
    if (seg.type !== "cite" || !seg.kind) continue;
    const kinds = out.get(seg.label) || [];
    if (!kinds.includes(seg.kind)) kinds.push(seg.kind);
    out.set(seg.label, kinds);
  }
  return out;
}

/** Lower case, letters and digits only (any language), single spaces: used to recognise a benchmark question that was typed in. */
export function normalizeQuestion(q) {
  return String(q).toLowerCase().normalize("NFC").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

/** Languages the benchmark has questions in. `key` matches the dataset's language names. */
export const LANGUAGES = [
  { key: "english", label: "English" },
  { key: "french", label: "Fran\u00e7ais" },
  { key: "spanish", label: "Espa\u00f1ol" },
  { key: "german", label: "Deutsch" },
  { key: "italian", label: "Italiano" },
  { key: "portuguese", label: "Portugu\u00eas" },
];

/** The question text in one language ("english" is the original); empty string when there is no translation. */
export function questionIn(entry, language) {
  if (!language || language === "english") return entry.question;
  return (entry.translations && entry.translations[language]) || "";
}

/** The benchmark entry whose question (in any language) equals the typed question, or null. */
export function findGolden(questions, typed) {
  const key = normalizeQuestion(typed);
  if (!key) return null;
  for (const q of questions) {
    if (normalizeQuestion(q.question) === key) return q;
    if (Object.values(q.translations || {}).some((t) => normalizeQuestion(t) === key)) return q;
  }
  return null;
}

/**
 * A random benchmark question in the language, as {entry, text}; null when none exist. Questions whose id is in
 * `exclude` are skipped unless nothing else is left. `random` is injectable for tests.
 */
export function pickRandom(questions, language, random = Math.random, exclude = new Set()) {
  const all = questions.filter((q) => questionIn(q, language));
  const fresh = all.filter((q) => !exclude.has(q.id));
  const pool = fresh.length ? fresh : all;
  if (!pool.length) return null;
  const entry = pool[Math.min(pool.length - 1, Math.floor(random() * pool.length))];
  return { entry, text: questionIn(entry, language) };
}

/** Questions whose answer is in an annotated table that the search finds (see eval/add_evidence_regions.py). */
export function tableQuestions(questions) {
  return questions.filter((q) => q.table_first);
}

/** The benchmark's annotated answer regions on one page, as {types, boxes} with boxes as page fractions; null when none. */
export function evidenceOn(entry, corpusId) {
  const ev = entry && entry.evidence && entry.evidence[String(corpusId)];
  return ev && Array.isArray(ev.boxes) && ev.boxes.length ? ev : null;
}

/**
 * Geometry for an enlarged view of one region of a page image of natural size natW x natH. The view keeps at least
 * minW x minH of the page (as fractions) around the region for context, and fits within maxW x maxH pixels.
 * Returns the view size, the background size and offset, and where the region sits inside the view (pixels).
 */
export function zoomGeometry(box, natW, natH, { maxW = 560, maxH = 420, minW = 0.45, minH = 0.12, pad = 0.01 } = {}) {
  const span = (lo, hi, min) => {
    let a = Math.max(0, lo - pad), b = Math.min(1, hi + pad);
    if (b - a < min) {
      const mid = (a + b) / 2;
      a = Math.max(0, Math.min(1 - min, mid - min / 2));
      b = Math.min(1, a + min);
    }
    return [a, b];
  };
  const [x1, x2] = span(box[0], box[2], minW);
  const [y1, y2] = span(box[1], box[3], minH);
  const scale = Math.min(maxW / ((x2 - x1) * natW), maxH / ((y2 - y1) * natH));
  const px = (f, n) => f * n * scale;
  return {
    width: Math.round(px(x2 - x1, natW)), height: Math.round(px(y2 - y1, natH)),
    bgWidth: Math.round(natW * scale), bgHeight: Math.round(natH * scale),
    offsetX: -Math.round(px(x1, natW)), offsetY: -Math.round(px(y1, natH)),
    mark: { left: Math.round(px(box[0] - x1, natW)), top: Math.round(px(box[1] - y1, natH)),
      width: Math.round(px(box[2] - box[0], natW)), height: Math.round(px(box[3] - box[1], natH)) },
  };
}

/** The questions marked as ones where Rerank clearly helps (see eval/mark_rerank_wins.py). */
export function rerankExamples(questions) {
  return questions.filter((q) => q.rerank_example);
}

/** Retrieval time (embedding, search, rerank) in ms from a timings object; the answer step is not included. */
export function retrievalMs(timings) {
  return (timings.embed_query || 0) + (timings.search || 0) + (timings.rerank || 0);
}

/**
 * Did the results contain the benchmark's known answer pages? No scores involved.
 * Returns {found, foundCount, total, foundIds}; found is true when at least one known answer page is in the results.
 */
export function goldenStatus(entry, results) {
  const gold = new Set(entry.gold);
  const foundIds = new Set(results.map((r) => r.corpus_id).filter((id) => gold.has(id)));
  const ranks = results.map((r, i) => (gold.has(r.corpus_id) ? (r.rank ?? i + 1) : null)).filter((r) => r !== null);
  return { found: foundIds.size > 0, foundCount: foundIds.size, total: gold.size, foundIds, bestRank: ranks.length ? Math.min(...ranks) : null };
}

/** For each reranked page, where it stood in the embed-only list (null when it was not in that list). */
export function rankMoves(base, reranked) {
  const was = new Map(base.map((s, i) => [s.corpus_id, s.rank ?? i + 1]));
  return reranked.map((s, i) => {
    const now = s.rank ?? i + 1;
    const from = was.has(s.corpus_id) ? was.get(s.corpus_id) : null;
    return { corpusId: s.corpus_id, from, delta: from === null ? null : from - now };
  });
}

/** Short words for a move, for example "Up 3 (was #6)". */
export function moveLabel(move) {
  if (move.from === null) return "New (was below #10)";
  if (move.delta === 0) return `Same position (#${move.from})`;
  return move.delta > 0 ? `Up ${move.delta} (was #${move.from})` : `Down ${-move.delta} (was #${move.from})`;
}

export function isParsed(source) {
  return source.text_source === "parse_cache" || source.text_source === "parse_live";
}

/** Steps a page went through, for example ["Parse", "Embed", "Rerank"]. */
export function pathSteps(source, mode) {
  const steps = [];
  if (isParsed(source)) steps.push("Parse");
  steps.push("Embed");
  if (mode === "dense_text_rerank") steps.push("Rerank");
  return steps;
}

/** "Parse + Embed + Rerank", "Embed + Rerank", ... (the endings of the decision tree). */
export function pathLabel(source, mode) {
  return pathSteps(source, mode).join(" + ");
}

/** How many of the pages had their text read by Parse. */
export function textSourceCounts(sources) {
  const parsed = sources.filter(isParsed).length;
  return { parsed, document: sources.length - parsed, total: sources.length };
}

/** One sentence about where the page text came from. */
export function textSummary(sources) {
  const c = textSourceCounts(sources);
  if (!c.total) return "";
  if (c.parsed === 0) return `All ${c.total} pages use the document's own text.`;
  if (c.document === 0) return `All ${c.total} pages were read with Parse.`;
  return `${c.parsed} of ${c.total} pages were read with Parse; the other ${c.document} use the document's own text.`;
}

/** Plain-language description of how a search ran, for the "how these pages were found" line. */
export function searchDescription(mode) {
  if (mode === "dense_text_rerank") return "Searched page text with Embed Pro, then reordered the best candidates with Rerank.";
  return "Searched page text with Embed Pro.";
}

const NUMBER_LIKE = /^[+-]?[\d.,]+%?$/;

/**
 * One CSV cell. Text that a spreadsheet could run as a formula (starts with = + - @) gets a leading apostrophe,
 * except plain numbers such as -12.5 or +3%, because the cells come from documents we do not control.
 */
export function csvCell(value) {
  let s = String(value ?? "").replace(/\r?\n/g, " ");
  if (/^[=+\-@]/.test(s) && !NUMBER_LIKE.test(s) && s !== "-") s = `'${s}`;
  return /[",]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV text for a header row and body rows. */
export function toCsv(header, rows) {
  return [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** A body row that only names a section (first cell filled, every other cell empty). */
export function isSectionRow(row) {
  return Boolean(row[0]) && row.slice(1).every((c) => !c);
}

/** "12 of 28" style text. */
export function ofTotal(count, total) {
  return `${count} of ${total}`;
}

/** "0.4 s" or "850 ms" for the step timings. */
export function formatMs(ms) {
  if (ms === undefined || ms === null) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

const LINE_TAG = /^<(tr|br|p|li|h\d|caption)\b|^<\/(table|p|li|h\d|div|caption)\s*>/i;
const CELL_TAG = /^<(td|th)\b/i;

/**
 * A page's text split into runs that were or were not sent to Claude, for display.
 * spans are Context Focus character offsets into text. HTML table markup becomes one line per row with " | " between
 * cells, and the sent flag of every visible character comes from its offset in the original text.
 */
export function focusSegments(text, spans = []) {
  const src = String(text || "");
  const sent = new Uint8Array(src.length);
  for (const s of spans) for (let i = Math.max(0, s.start); i < Math.min(src.length, s.end); i += 1) sent[i] = 1;
  const out = [];
  const push = (chunk, isSent) => {
    if (!chunk) return;
    const last = out.at(-1);
    if (last && last.sent === isSent) last.text += chunk;
    else out.push({ text: chunk, sent: isSent });
  };
  let firstCell = true;
  let i = 0;
  while (i < src.length) {
    if (src[i] === "<") {
      const close = src.indexOf(">", i);
      if (close !== -1 && close - i < 200) {
        const tag = src.slice(i, close + 1);
        if (CELL_TAG.test(tag)) {
          if (!firstCell) push(" | ", Boolean(sent[i]));
          firstCell = false;
        } else if (LINE_TAG.test(tag)) {
          push("\n", Boolean(sent[i]));
          firstCell = true;
        }
        i = close + 1;
        continue;
      }
    }
    push(src[i], Boolean(sent[i]));
    i += 1;
  }
  for (const seg of out) seg.text = seg.text.replace(/[ \t]*\n[ \t]*/g, "\n").replace(/\n{3,}/g, "\n\n");
  return out;
}

/** "about 1,234" with thousands separators, for token and character counts. */
export function formatCount(n) {
  return Number(n || 0).toLocaleString("en-US");
}

/** One line on what the assistant was sent: pages, images or not, text characters and Claude's input tokens. */
export function sentSummary(sources, claude, contextFocus, citedCount, images = true) {
  const n = sources.length;
  const pages = `${n} ${n === 1 ? "page" : "pages"}`;
  const tokens = claude && claude.input_tokens ? ` Claude read ${formatCount(claude.input_tokens)} input tokens.` : "";
  const cited = ` ${citedCount} cited in this answer.`;
  const were = n === 1 ? "was" : "were";
  if (!claude) return `${pages} ${were} sent to the assistant.${cited}`;
  const image = images ? "its image and " : "";
  const noImages = images ? "" : " No page images were sent.";
  if (!contextFocus) {
    return `${pages} ${were} sent to the assistant, each as ${image}its text (${formatCount(claude.sent_chars)} characters in all).${noImages}${tokens}${cited}`;
  }
  return `${pages} ${were} sent to the assistant, each as ${image}only the passages Context Focus picked: `
    + `${formatCount(claude.sent_chars)} of ${formatCount(claude.page_chars)} characters of text.${noImages}${tokens}${cited}`;
}

/** The highest-scoring Context Focus passage of a page as one plain line, cut on a word boundary; null when none. */
export function bestPassage(text, spans = [], limit = 420) {
  if (!spans.length) return null;
  const best = spans.reduce((a, b) => (b.score > a.score ? b : a));
  const plain = focusSegments(String(text || "").slice(best.start, best.end)).map((s) => s.text).join("")
    .replace(/\s*\n\s*/g, " / ").replace(/\s+/g, " ").replace(/^( \/ )+|( \/ )+$/g, "").trim();
  const cut = plain.length <= limit ? plain : `${plain.slice(0, limit).replace(/\s+\S*$/, "")}...`;
  return { text: cut, score: best.score };
}
