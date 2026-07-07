// Minimal, safe markdown → HTML for answers. Escapes first, then applies a
// small subset (bold, italics, inline code, headings, lists, [N] markers).
// No raw model output reaches the DOM unescaped.

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string
  );
}

function inline(s: string): string {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d{1,2})\]/g, '<sup class="marker" data-m="$1">$1</sup>');
}

export function renderMarkdown(raw: string): string {
  const lines = esc(raw).split("\n");
  let html = "";
  let list: { tag: string; items: string[] } | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      html += `<p>${para.map(inline).join("<br>")}</p>`;
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      html += `<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`;
      list = null;
    }
  };
  for (const line of lines) {
    const t = line.trim();
    const ul = t.match(/^[-•*]\s+(.*)/);
    const ol = t.match(/^\d{1,2}[.)]\s+(.*)/);
    const h = t.match(/^#{1,4}\s+(.*)/);
    if (!t) {
      flushPara();
      flushList();
    } else if (ul) {
      flushPara();
      if (!list || list.tag !== "ul") {
        flushList();
        list = { tag: "ul", items: [] };
      }
      list.items.push(ul[1]);
    } else if (ol) {
      flushPara();
      if (!list || list.tag !== "ol") {
        flushList();
        list = { tag: "ol", items: [] };
      }
      list.items.push(ol[1]);
    } else if (h) {
      flushPara();
      flushList();
      html += `<h4>${inline(h[1])}</h4>`;
    } else {
      flushList();
      para.push(t);
    }
  }
  flushPara();
  flushList();
  return html;
}
