import { signIn, completeNewPassword, getIdToken, currentEmail, signOut } from "./auth.js";
import { LANGUAGES, questionIn, pickRandom, retrievalMs, docName, parseCitations, citedLabels, citedKinds, PAGE_KINDS, findGolden, goldenStatus, rerankExamples, tableQuestions, evidenceOn, zoomGeometry, rankMoves, moveLabel, pathLabel, textSummary, searchDescription, formatMs, toCsv, isSectionRow, focusSegments, formatCount, sentSummary, bestPassage } from "./logic.js";

const cfg = window.FRW_CONFIG || {};
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- helpers
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

/** Only show images served from AWS storage over HTTPS. */
function safeImageUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" && u.hostname.endsWith(".amazonaws.com") ? u.href : "";
  } catch {
    return "";
  }
}

/** Renders text with **bold** and blank-line paragraphs using DOM nodes only (never innerHTML). */
function renderRich(target, text) {
  target.replaceChildren();
  for (const para of String(text).split(/\n{2,}/)) {
    const p = el("p");
    para.split("**").forEach((part, i) => p.append(i % 2 ? el("strong", { text: part }) : document.createTextNode(part)));
    target.append(p);
  }
}

function show(node, visible) {
  node.hidden = !visible;
}

async function api(path, options = {}) {
  const token = await getIdToken();
  if (!token) {
    showLogin();
    throw new Error("Please sign in again.");
  }
  const res = await fetch(`${cfg.apiBase}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  if (res.status === 401) {
    signOut();
    showLogin();
    throw new Error("Your session expired. Please sign in again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.message || `Request failed (${res.status}).`);
  return data;
}

// ---------------------------------------------------------------- sign in
let pendingChallenge = null;

function showLogin() {
  show($("view-login"), true);
  show($("view-app"), false);
  show($("session"), false);
}

function showApp() {
  show($("view-login"), false);
  show($("view-app"), true);
  show($("session"), true);
  $("user-email").textContent = currentEmail();
}

function loginError(message) {
  $("login-error").textContent = message || "";
  show($("login-error"), Boolean(message));
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError("");
  try {
    const out = await signIn($("login-email").value.trim(), $("login-password").value);
    if (out.challenge) {
      pendingChallenge = out;
      show($("login-form"), false);
      show($("new-password-form"), true);
      $("new-password").focus();
    } else {
      $("login-password").value = "";
      showApp();
    }
  } catch (err) {
    loginError(err.message);
  }
});

$("new-password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError("");
  try {
    await completeNewPassword(pendingChallenge, $("new-password").value);
    $("new-password").value = "";
    pendingChallenge = null;
    show($("new-password-form"), false);
    show($("login-form"), true);
    showApp();
  } catch (err) {
    loginError(err.message);
  }
});

$("sign-out").addEventListener("click", () => {
  signOut();
  showLogin();
});

// ---------------------------------------------------------------- tabs
const TABS = ["ask", "search", "parse"];
function selectTab(name) {
  for (const t of TABS) {
    const tab = $(`tab-${t}`);
    tab.setAttribute("aria-selected", String(t === name));
    tab.tabIndex = t === name ? 0 : -1;
    show($(`panel-${t}`), t === name);
  }
  if (name === "parse") loadParseTab();
  if (name === "search") loadGolden();
}
for (const t of TABS) {
  $(`tab-${t}`).addEventListener("click", () => selectTab(t));
  $(`tab-${t}`).addEventListener("keydown", (e) => {
    const i = TABS.indexOf(t);
    const next = { ArrowRight: TABS[(i + 1) % TABS.length], ArrowLeft: TABS[(i + TABS.length - 1) % TABS.length], Home: TABS[0], End: TABS.at(-1) }[e.key];
    if (!next) return;
    e.preventDefault();
    selectTab(next);
    $(`tab-${next}`).focus();
  });
}

// ---------------------------------------------------------------- benchmark questions and languages
let golden = null;
let lang = "english";

async function loadGolden() {
  if (golden) return golden;
  try {
    const res = await fetch("examples/golden-questions.json");
    const data = await res.json();
    golden = data.questions;
  } catch {
    golden = [];
  }
  return golden;
}

function langLabel(key) {
  return (LANGUAGES.find((l) => l.key === key) || {}).label || key;
}

/** If a box holds a benchmark question, show it in the chosen language so switching language translates it. */
function translateBox(box) {
  const entry = golden && findGolden(golden, box.value);
  const text = entry && questionIn(entry, lang);
  if (text) box.value = text;
}

function renderLangChips() {
  for (const id of ["ask-langs", "search-langs"]) {
    $(id).replaceChildren(...LANGUAGES.map((l) => {
      const b = el("button", { type: "button", "aria-pressed": String(l.key === lang), text: l.label });
      b.addEventListener("click", async () => {
        lang = l.key;
        renderLangChips();
        await loadGolden();
        translateBox($("question"));
        translateBox($("search-question"));
        sampleNote(null);
      });
      return b;
    }));
  }
}

function sampleNote(text) {
  $("ask-sample-note").textContent = text || "";
  show($("ask-sample-note"), Boolean(text));
}

// ---------------------------------------------------------------- assistant
function askMode() {
  return $("ask-rerank").checked ? "dense_text_rerank" : "dense_text";
}

function focusOn() {
  return $("ask-rerank").checked && $("ask-focus").checked;
}

function syncAskHint() {
  const reranked = $("ask-rerank").checked;
  $("ask-rerank-hint").textContent = reranked ? "Embed + Rerank: best ordering, slower" : "Embed only: faster, no reordering";
  $("ask-focus").disabled = !reranked;
  $("ask-focus-hint").textContent = !reranked ? "needs Rerank"
    : $("ask-focus").checked ? "send Claude only the relevant passages" : "send Claude each page's full text";
  $("ask-images-hint").textContent = $("ask-images").checked ? "Claude checks figures against each page"
    : "text only: fewest tokens, no check against the page";
}
$("ask-rerank").addEventListener("change", syncAskHint);
$("ask-focus").addEventListener("change", syncAskHint);
$("ask-images").addEventListener("change", syncAskHint);

function flash(node) {
  node.classList.add("flash");
  setTimeout(() => node.classList.remove("flash"), 1600);
}

/** Answer text with **bold**, paragraphs and clickable numbered citations, built from DOM nodes only. */
function renderAnswer(target, text, sources) {
  const byLabel = new Map(sources.map((s) => [s.label, s]));
  target.replaceChildren();
  for (const para of String(text).split(/\n{2,}/)) {
    const p = el("p");
    para.split("**").forEach((part, i) => {
      const holder = i % 2 ? el("strong") : p;
      for (const seg of parseCitations(part)) {
        if (seg.type === "text") {
          holder.append(document.createTextNode(seg.text));
          continue;
        }
        const src = byLabel.get(seg.label);
        if (!src) {
          holder.append(el("span", { class: "cite missing", title: "This page was not among the pages sent to the assistant", text: seg.label }));
          continue;
        }
        const chip = el("button", { class: "cite", type: "button", title: `${docName(src.doc_id)}, page ${src.page}`,
          "aria-label": `Citation ${src.rank}: ${docName(src.doc_id)}, page ${src.page}`, text: String(src.rank) });
        chip.addEventListener("click", () => {
          const card = $(`source-${src.rank}`);
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          flash(card);
        });
        holder.append(chip);
        if (seg.kind) holder.append(el("span", { class: `cite-kind kind-${seg.kind}`, text: PAGE_KINDS[seg.kind].toLowerCase() }));
      }
      if (i % 2) p.append(holder);
    });
    target.append(p);
  }
}

// ---------------------------------------------------------------- evidence highlights
let zoom = null;

function zoomBox() {
  if (zoom) return zoom;
  const page = el("div", { class: "zoom-page" }, el("div", { class: "zoom-mark" }));
  zoom = el("div", { class: "evidence-zoom", role: "tooltip", id: "evidence-zoom", hidden: "" }, page, el("p", { class: "zoom-caption" }));
  document.body.append(zoom);
  return zoom;
}

function showZoom(img, box, anchor, caption) {
  if (!img.naturalWidth) return;
  const z = zoomBox();
  const maxW = Math.min(720, window.innerWidth - 32);
  const g = zoomGeometry(box, img.naturalWidth, img.naturalHeight, { maxW, maxH: Math.min(460, window.innerHeight - 96), minW: 0.6, minH: 0.14 });
  const page = z.firstChild;
  Object.assign(page.style, { width: `${g.width}px`, height: `${g.height}px`, backgroundImage: `url("${img.currentSrc || img.src}")`,
    backgroundSize: `${g.bgWidth}px ${g.bgHeight}px`, backgroundPosition: `${g.offsetX}px ${g.offsetY}px` });
  Object.assign(page.firstChild.style, { left: `${g.mark.left}px`, top: `${g.mark.top}px`, width: `${g.mark.width}px`, height: `${g.mark.height}px` });
  z.lastChild.textContent = caption;
  z.hidden = false;
  const r = anchor.closest("figure").getBoundingClientRect();
  const w = g.width + 18, h = z.offsetHeight;
  const left = r.right + 12 + w <= window.innerWidth ? r.right + 12 : Math.max(8, r.left - 12 - w);
  const top = Math.min(Math.max(8, r.top), window.innerHeight - h - 8);
  Object.assign(z.style, { left: `${left}px`, top: `${Math.max(8, top)}px` });
}

function hideZoom() {
  if (zoom) zoom.hidden = true;
}

/** The page image, with the benchmark's annotated answer regions drawn over it when there are any. */
function pageImage(src, alt, ev) {
  const img = el("img", { src, alt, loading: "lazy" });
  if (!ev) return img;
  const kind = ev.types.filter((t) => !t.startsWith("N/A")).join(" and ").toLowerCase() || "answer";
  const caption = `Marked by the benchmark's annotators as the answer region (${kind}).`;
  const frame = el("div", { class: "page-frame" }, img);
  const area = (b) => (b[2] - b[0]) * (b[3] - b[1]);
  [...ev.boxes].sort((a, b) => area(b) - area(a)).forEach((b, i) => {
    const mark = el("button", { type: "button", class: "evidence-box", "aria-describedby": "evidence-zoom",
      "aria-label": `Enlarge marked answer region ${i + 1}` });
    Object.assign(mark.style, { left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%` });
    const open = () => showZoom(img, b, mark, caption);
    mark.addEventListener("mouseenter", open);
    mark.addEventListener("focus", open);
    mark.addEventListener("click", open);
    mark.addEventListener("mouseleave", hideZoom);
    mark.addEventListener("blur", hideZoom);
    frame.append(mark);
  });
  return frame;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideZoom(); });
window.addEventListener("scroll", hideZoom, { passive: true });

/** Page text with the Context Focus passages highlighted and the rest greyed out. */
function highlightedText(text, spans) {
  const body = el("pre", { class: "page-text focused" });
  for (const seg of focusSegments(text, spans)) body.append(seg.sent ? el("mark", { text: seg.text }) : el("span", { class: "left-out", text: seg.text }));
  return body;
}

/** The page text Claude was given: under Context Focus the picked passages are highlighted and the rest is greyed out. */
function sentText(s, contextFocus, images = true) {
  if (typeof s.text !== "string") return null;
  const spans = s.spans || [];
  const total = s.text.length;
  const sentChars = contextFocus ? spans.reduce((n, sp) => n + Math.max(0, sp.end - sp.start), 0) : total;
  const imageOnly = images ? "so Claude read the image only" : "and no image was sent, so Claude got nothing from this page";
  const label = !total ? `No text on this page, ${imageOnly}`
    : contextFocus
      ? (spans.length ? `Text sent to Claude: ${spans.length} ${spans.length === 1 ? "passage" : "passages"}, ${formatCount(sentChars)} of ${formatCount(total)} characters`
        : `Text sent to Claude: no passage matched, ${imageOnly}`)
      : `Text sent to Claude: the full page text, ${formatCount(total)} characters`;
  const body = contextFocus ? highlightedText(s.text, spans) : el("pre", { class: "page-text", text: focusSegments(s.text).map((g) => g.text).join("") });
  const details = el("details", { class: "sent-text" }, el("summary", { text: label }), total ? body : null);
  if (contextFocus && spans.length) details.open = true;
  return details;
}

function renderSources(sources, cited, mode, kinds = new Map(), entry = null, contextFocus = false, images = true) {
  const box = $("sources");
  box.replaceChildren();
  let highlighted = 0;
  for (const s of sources) {
    const src = safeImageUrl(s.image_url);
    const isCited = cited.has(s.label);
    const found = (kinds.get(s.label) || []).map((k) => PAGE_KINDS[k]);
    const ev = evidenceOn(entry, s.corpus_id);
    if (ev && src) highlighted += 1;
    const badges = el("p", { class: "badges" },
      el("span", { class: `badge ${isCited ? "ok" : "muted"}`, text: isCited ? "Cited in answer" : "Not cited" }),
      found.length ? el("span", { class: "badge kind", text: `Answer taken from: ${found.join(", ").toLowerCase()}` }) : null,
      ev && src ? el("span", { class: "badge marked", text: "Answer region highlighted" }) : null,
      contextFocus ? el("span", { class: "badge focus", text: (s.spans || []).length ? "Context Focus passages" : "No passage matched" }) : null,
      images ? null : el("span", { class: "badge muted", text: "Image not sent to Claude" }),
      el("span", { class: "badge path", text: pathLabel(s, mode) }));
    box.append(el("figure", { id: `source-${s.rank}`, class: "source" },
      el("figcaption", { class: "source-head" }, el("span", { class: "rank", text: String(s.rank) }),
        el("span", { text: `${docName(s.doc_id)}, page ${s.page}` })),
      src ? pageImage(src, `Page image: ${docName(s.doc_id)}, page ${s.page}`, ev) : null,
      badges, sentText(s, contextFocus, images)));
  }
  return highlighted;
}

function renderHow(timings, sources, mode, contextFocus = false, claude = null, images = true) {
  const reranked = mode === "dense_text_rerank";
  const tokens = claude && claude.input_tokens ? ` It read ${formatCount(claude.input_tokens)} input tokens.` : "";
  const steps = [
    { title: "Your question", detail: "Turned into a vector by Embed Pro", ms: formatMs(timings.embed_query) },
    { title: "Vector search", detail: "Finds the pages whose text is closest in meaning", ms: formatMs(timings.search) },
    reranked
      ? { title: "Rerank", detail: "Rerank reorders 30 candidates and keeps the best five", ms: formatMs(timings.rerank) }
      : { title: "Rerank: off", detail: "The top five pages come straight from the vector search", ms: "skipped", skipped: true },
    contextFocus
      ? { title: "Context Focus", detail: claude
          ? `Rerank picks the passages in each page that answer the question: ${formatCount(claude.sent_chars)} of ${formatCount(claude.page_chars)} characters go to Claude`
          : "Rerank picks the passages in each page that answer the question", ms: formatMs(timings.focus) }
      : { title: "Context Focus: off", detail: "Claude gets each page's full text", ms: "skipped", skipped: true },
    { title: "Answer", detail: `Claude reads those pages as ${images ? "images and " : ""}${contextFocus ? "passages" : "text"}${images ? "" : " only"}, writes the answer and cites them, saying whether each fact is in a table, chart or text.${tokens}`, ms: formatMs(timings.generate) },
  ];
  $("how-steps").replaceChildren(...steps.map((st) => el("li", { class: st.skipped ? "skipped" : "" },
    el("strong", { text: st.title }), el("span", { text: st.detail }), el("em", { text: st.ms }))));
  $("how-mode").textContent = `${reranked ? "Embed + Rerank" : "Embed only"}${contextFocus ? " + Context Focus" : ""}${images ? "" : ", text only"}: retrieval ${formatMs(retrievalMs(timings))}`;
  show($("how-note"), images);
  $("how-summary").textContent = `${searchDescription(mode)} ${textSummary(sources)}`;
}

async function ask(question) {
  const button = $("ask-button");
  button.disabled = true;
  $("ask-error").hidden = true;
  show($("ask-result"), false);
  show($("ask-skeleton"), true);
  $("ask-status").textContent = "Finding the pages and writing the answer...";
  show($("ask-status"), true);
  const mode = askMode();
  try {
    const out = await api("/query", { method: "POST", body: JSON.stringify({ question, mode, context_focus: focusOn(), images: $("ask-images").checked }) });
    const sources = out.sources || [];
    const cited = citedLabels(out.answer || "");
    const usedMode = out.mode || mode;
    const timings = out.timings_ms || {};
    $("ask-echo").textContent = question;
    renderAnswer($("answer"), out.answer || "", sources);
    const citedCount = sources.filter((s) => cited.has(s.label)).length;
    const contextFocus = Boolean(out.context_focus);
    const images = out.images !== false;
    $("sent-summary").textContent = sentSummary(sources, out.claude, contextFocus, citedCount, images);
    renderHow(timings, sources, usedMode, contextFocus, out.claude, images);
    hideZoom();
    const highlighted = renderSources(sources, cited, usedMode, citedKinds(out.answer || ""), findGolden(golden || [], question), contextFocus, images);
    show($("focus-legend"), contextFocus);
    show($("focus-legend-images"), images);
    $("sources-note").textContent = "These are the only pages the assistant could read. Select a number in the answer to jump to its page."
      + (highlighted ? " The highlighted box is where the benchmark's annotators marked the answer; hover over it to enlarge." : "");
    show($("ask-result"), true);
  } catch (err) {
    $("ask-error").textContent = err.message;
    show($("ask-error"), true);
  } finally {
    show($("ask-status"), false);
    show($("ask-skeleton"), false);
    button.disabled = false;
  }
}

$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("question").value.trim());
});
const TABLE_FIRST_PICKS = 3;
const askPicked = new Set();
$("ask-random").addEventListener("click", async () => {
  await loadGolden();
  const tables = askPicked.size < TABLE_FIRST_PICKS ? pickRandom(tableQuestions(golden), lang, Math.random, askPicked) : null;
  const pick = tables || pickRandom(golden, lang, Math.random, askPicked);
  if (!pick) return;
  askPicked.add(pick.entry.id);
  $("question").value = pick.text;
  sampleNote(lang === "english"
    ? `A random benchmark question${tables ? " whose answer is in a table" : ""}. Switch the language above to see the same question in another language.`
    : `The same benchmark question in ${langLabel(lang)}. The filings are in English, so the pages are found across languages and the answer comes back in ${langLabel(lang)}.`);
  $("ask-button").focus();
});

// ---------------------------------------------------------------- search (no assistant)
$("search-question").addEventListener("input", () => show($("rerank-note"), false));
$("golden-random").addEventListener("click", async () => {
  await loadGolden();
  const pick = pickRandom(golden, lang);
  if (!pick) return;
  $("search-question").value = pick.text;
  show($("rerank-note"), false);
  search(pick.text, false);
});
$("golden-rerank").addEventListener("click", async () => {
  await loadGolden();
  const pick = pickRandom(rerankExamples(golden), lang);
  if (!pick) return;
  $("search-question").value = pick.text;
  show($("rerank-note"), true);
  search(pick.text, true);
});

function countText(status, shown) {
  const n = status.foundCount;
  if (!status.found) return `no known answer pages in the top ${shown}`;
  return `${n} known answer ${n === 1 ? "page" : "pages"} in the top ${shown} (best at #${status.bestRank})`;
}

function renderGoldenBanner(entry, base, reranked, shown) {
  show($("golden-banner"), Boolean(entry));
  show($("not-golden"), !entry);
  if (!entry) return;
  const found = base.found || Boolean(reranked && reranked.found);
  const banner = $("golden-banner");
  banner.classList.toggle("found", found);
  banner.classList.toggle("missed", !found);
  $("golden-headline").textContent = found ? "Known answer page found" : "Known answer page not found";
  const detail = reranked
    ? `Embed: ${countText(base, shown)}. Embed + Rerank: ${countText(reranked, shown)}.`
    : `${countText(base, shown)[0].toUpperCase()}${countText(base, shown).slice(1)}.`;
  $("golden-detail").textContent = detail;
  renderRich($("golden-reference-text"), entry.reference_answer || "Not available");
}

function renderResults(target, sources, mode, status, moves) {
  target.replaceChildren(...sources.map((s, i) => {
    const src = safeImageUrl(s.image_url);
    const isGold = status && status.foundIds.has(s.corpus_id);
    const move = moves && moves[i];
    const thumb = src ? el("a", { href: src, target: "_blank", rel: "noopener", "aria-label": `Open page image: ${docName(s.doc_id)}, page ${s.page}` },
      el("img", { src, alt: "", loading: "lazy" })) : null;
    return el("li", { class: `result${isGold ? " gold" : ""}` },
      thumb ? el("div", { class: "thumb" }, thumb) : null,
      el("div", { class: "result-body" },
        el("h4", {}, el("span", { class: "rank", text: String(s.rank) }), document.createTextNode(` ${docName(s.doc_id)}, page ${s.page}`),
          isGold ? el("span", { class: "badge ok", text: "Known answer page" }) : null,
          move ? el("span", { class: `badge ${move.delta === null || move.delta > 0 ? "up" : move.delta < 0 ? "down" : "muted"}`, text: `${move.delta === null || move.delta > 0 ? "\u25B2 " : move.delta < 0 ? "\u25BC " : ""}${moveLabel(move)}` }) : null),
        resultText(s)));
  }));
}

/** A result's best Context Focus passage, highlighted, with the whole page text one click away; the plain snippet otherwise. */
function resultText(s) {
  if (!Array.isArray(s.spans)) return el("p", { class: "snippet", text: s.snippet || "(no text on this page)" });
  const best = bestPassage(s.text, s.spans);
  if (!best) {
    return el("div", {}, el("p", { class: "snippet", text: s.snippet || "(no text on this page)" }),
      el("p", { class: "muted small-text", text: "Context Focus found no passage on this page that matches the question." }));
  }
  const n = s.spans.length;
  return el("div", {},
    el("p", { class: "snippet passage" }, el("span", { class: "passage-label", text: "Context Focus passage" }), el("mark", { text: best.text })),
    el("details", { class: "sent-text" }, el("summary", { text: `Page text with ${n === 1 ? "the passage" : `all ${n} passages`} highlighted` }),
      highlightedText(s.text, s.spans)));
}

async function retrieve(question, mode) {
  const out = await api("/query", { method: "POST", body: JSON.stringify({ question, mode, generate: false, context_focus: mode === "dense_text_rerank" }) });
  return { sources: out.sources || [], mode: out.mode || mode, timings: out.timings_ms || {} };
}

async function search(question, compare = false) {
  const buttons = [$("golden-random"), $("golden-rerank")];
  for (const b of buttons) b.disabled = true;
  $("search-error").hidden = true;
  show($("search-result"), false);
  $("search-status").textContent = compare ? "Searching with and without Rerank..." : "Searching...";
  show($("search-status"), true);
  try {
    await loadGolden();
    const [base, reranked] = await Promise.all([
      retrieve(question, "dense_text"),
      compare ? retrieve(question, "dense_text_rerank") : Promise.resolve(null),
    ]);
    const entry = findGolden(golden || [], question);
    const baseStatus = entry ? goldenStatus(entry, base.sources) : null;
    const rerankStatus = entry && reranked ? goldenStatus(entry, reranked.sources) : null;
    renderGoldenBanner(entry, baseStatus, rerankStatus, base.sources.length);
    $("search-path").textContent = compare
      ? "Both columns searched the same page text with Embed Pro. The right column then reordered the best 30 candidates with Rerank, and its Context Focus picked each page's best-matching passage. Arrows show how each page moved."
      : `${searchDescription(base.mode)} ${textSummary(base.sources)}`;
    $("col-embed-title").textContent = compare ? "Embed" : "Results";
    const focusMs = (t) => (t.focus !== undefined ? ` Context Focus ${formatMs(t.focus)}.` : "");
    $("col-embed-meta").textContent = (compare ? `Vector search only. Retrieval ${formatMs(retrievalMs(base.timings))}.` : `Retrieval ${formatMs(retrievalMs(base.timings))}.`) + focusMs(base.timings);
    renderResults($("results"), base.sources, base.mode, baseStatus, null);
    show($("col-rerank"), compare);
    $("results-grid").classList.toggle("two", compare);
    if (reranked) {
      $("col-rerank-meta").textContent = `Embed, then Rerank. Retrieval ${formatMs(retrievalMs(reranked.timings))}.${focusMs(reranked.timings)}`;
      renderResults($("results-rerank"), reranked.sources, reranked.mode, rerankStatus, rankMoves(base.sources, reranked.sources));
    }
    show($("search-result"), true);
  } catch (err) {
    $("search-error").textContent = err.message;
    show($("search-error"), true);
  } finally {
    show($("search-status"), false);
    for (const b of buttons) b.disabled = false;
  }
}

$("search-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("search-form").requestSubmit();
  }
});
$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  show($("rerank-note"), false);
  search($("search-question").value.trim(), false);
});

// ---------------------------------------------------------------- shared answer columns (used by the Parse tab examples)
function verdictBadge(verdict) {
  const cls = verdict === "correct" ? "ok" : verdict === "partial" ? "warn" : "bad";
  return el("span", { class: `badge ${cls}`, text: verdict });
}

function answerColumn(title, answer) {
  const box = el("div", { class: "answer rich" });
  renderRich(box, answer ? answer.answer : "Not available");
  return el("div", {}, el("h5", { text: title }, answer ? verdictBadge(answer.verdict) : null), box);
}

// ---------------------------------------------------------------- parse tab
let parseLoaded = false;
let parseTables = [];
let currentCsv = "";
let currentCsvName = "table.csv";

async function loadJson(path) {
  try {
    const res = await fetch(path);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

function renderParseTable(t) {
  const table = $("parse-table");
  const head = el("tr", {}, ...t.header.map((h) => el("th", { scope: "col", text: h })));
  const body = t.rows.map((row) => {
    if (isSectionRow(row)) return el("tr", { class: "section" }, el("th", { scope: "rowgroup", colspan: String(t.header.length), text: row[0] }));
    return el("tr", {}, ...row.map((c, i) => (i === 0 ? el("th", { scope: "row", text: c }) : el("td", { text: c }))));
  });
  table.replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
  $("parse-table-caption").textContent = `${docName(t.doc_id)}, page ${t.page_label}: ${t.n_rows} rows by ${t.n_cols} columns, as Parse returned them.`;
  currentCsv = toCsv(t.header, t.rows);
  currentCsvName = `${t.doc_id}-p${t.page_label}.csv`;
  $("parse-copy-status").textContent = "";
}

async function showParsePage(t) {
  const img = $("parse-page-img"), link = $("parse-page-link"), status = $("parse-page-status");
  $("parse-page-caption").textContent = `The page: ${docName(t.doc_id)}, page ${t.page_label}`;
  link.hidden = true;
  status.hidden = false;
  status.textContent = "Loading the page...";
  try {
    const out = await api(`/page-image?key=${encodeURIComponent(t.image_key)}`);
    const src = safeImageUrl(out.url);
    if (!src) throw new Error("The page image is not available.");
    img.src = src;
    img.alt = `Page image: ${docName(t.doc_id)}, page ${t.page_label}`;
    link.href = src;
    link.hidden = false;
    status.hidden = true;
  } catch (err) {
    status.textContent = err.message;
  }
}

function selectParseTable() {
  const t = parseTables[Number($("parse-table-pick").value)];
  if (!t) return;
  renderParseTable(t);
  showParsePage(t);
}

$("parse-table-pick").addEventListener("change", selectParseTable);
$("parse-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(currentCsv);
    $("parse-copy-status").textContent = "Copied.";
  } catch {
    $("parse-copy-status").textContent = "Copying is not allowed here. Use Download CSV.";
  }
});
$("parse-download").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([currentCsv], { type: "text/csv;charset=utf-8" }));
  const a = el("a", { href: url, download: currentCsvName });
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

const SCAN_LEVEL_TEXT = {
  clean: "Clean pages (dataset text, no Parse)",
  light: "Light scan (about 150 dpi)",
  medium: "Medium scan (about 100 dpi, skewed, noisy)",
  heavy: "Poor scan (about 65 dpi, strongly degraded)",
};

const SCAN_FILTERS = {
  parse_wins: { label: "Parse text was right and the image alone was wrong", test: (e) => e.kind === "parse_wins" },
  image_wins: { label: "The image alone was right and Parse text was wrong", test: (e) => e.kind === "image_wins" },
};
let scanData = null;

function renderScanExample(e) {
  const pages = el("div", { class: "pages" }, ...e.pages.map((p) => el("figure", {},
    el("a", { href: p.image, target: "_blank", rel: "noopener" }, el("img", { src: p.image, alt: `Degraded scan of ${p.label}`, loading: "lazy" })),
    el("figcaption", { class: "muted", text: `${p.label} (${SCAN_LEVEL_TEXT[e.level] || e.level})` }))));
  const excerpt = el("details", { class: "reference" }, el("summary", { text: "What Parse read from the first page" }),
    el("pre", { class: "excerpt", text: e.pages[0].parse_excerpt || "(nothing)" }));
  return el("article", { class: "card" },
    el("h4", { text: e.question }),
    el("p", { class: "muted", text: `Reference answer: ${e.reference_answer}` }),
    pages,
    el("div", { class: "compare" },
      answerColumn("Answer from Parse text", e.answers.parse),
      answerColumn("Answer from the page image alone", e.answers.image)),
    excerpt);
}

function renderScanExamples() {
  const f = SCAN_FILTERS[$("scan-filter").value];
  const shown = scanData.examples.filter(f.test);
  $("scan-examples").replaceChildren(...(shown.length ? shown.map(renderScanExample) : [el("p", { class: "muted", text: "No examples in this group." })]));
}

async function loadParseTab() {
  if (parseLoaded) return;
  parseLoaded = true;
  const [tables, scans] = await Promise.all([loadJson("examples/parse-only.json"), loadJson("examples/scan-levels.json")]);
  if (tables && tables.examples.length) {
    parseTables = tables.examples;
    $("parse-table-pick").replaceChildren(...parseTables.map((t, i) => el("option", { value: String(i),
      text: `${docName(t.doc_id)}, page ${t.page_label} (${t.n_rows} rows)` })));
    $("parse-table-caveats").replaceChildren(...tables.caveats.map((c) => el("li", { text: c })));
    show($("parse-table-view"), true);
    selectParseTable();
  } else {
    $("parse-table-error").textContent = "The table examples could not be loaded.";
    show($("parse-table-error"), true);
  }
  if (!scans) {
    $("scan-error").textContent = "The scan results could not be loaded.";
    show($("scan-error"), true);
    return;
  }
  scanData = scans;
  $("scan-filter").replaceChildren(...Object.entries(SCAN_FILTERS).map(([k, f]) => el("option", { value: k, text: f.label })));
  $("scan-filter").addEventListener("change", renderScanExamples);
  show($("scan-example-controls"), true);
  $("scan-caveats").replaceChildren(...scans.caveats.map((c) => el("li", { text: c })));
  renderScanExamples();
}

// ---------------------------------------------------------------- start
document.title = cfg.appTitle || document.title;
$("app-title").textContent = cfg.appTitle || $("app-title").textContent;
syncAskHint();
renderLangChips();
loadGolden();
(async () => {
  if (await getIdToken()) showApp();
  else showLogin();
})();
