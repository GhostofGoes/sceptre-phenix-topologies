// Syntax check of the Mermaid blocks in Markdown files.
//
//   node check_mermaid.mjs FILE.md [FILE.md ...]
//
// The script runs mermaid.parse() on each ```mermaid block. jsdom stands in for
// the browser, so the check needs no Chromium. It finds a block that Mermaid
// cannot parse. It does not find a diagram that parses and then renders badly,
// and it does not know which Mermaid version GitHub runs.
//
// The prek hook `cm-mermaid` installs `mermaid` and `jsdom` in its own
// environment and names it in NODE_PATH. An ES module does not read NODE_PATH,
// so the script finds the two packages with require.resolve().
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const FENCE = /^(\s*)(`{3,}|~{3,})\s*mermaid\s*$/;

// The Mermaid blocks of a Markdown text, each with the line number of its first line.
function blocks(text) {
  const found = [];
  const lines = text.split(/\r?\n/);
  let open = null;
  for (const [index, line] of lines.entries()) {
    if (open === null) {
      const match = FENCE.exec(line);
      if (match) open = { indent: match[1].length, fence: match[2], line: index + 2, code: [] };
    } else if (line.trim().startsWith(open.fence) && line.trim().replace(/[`~]/g, "") === "") {
      found.push({ line: open.line, code: open.code.join("\n") });
      open = null;
    } else {
      open.code.push(line.slice(Math.min(open.indent, line.length - line.trimStart().length)));
    }
  }
  if (open !== null) found.push({ line: open.line, code: open.code.join("\n"), unclosed: true });
  return found;
}

const { JSDOM } = require("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
for (const key of ["window", "document", "DOMParser", "Element", "HTMLElement", "Node"]) {
  Object.defineProperty(globalThis, key, { value: dom.window[key] ?? dom.window, configurable: true });
}
Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true });
const mermaid = (await import(pathToFileURL(require.resolve("mermaid")).href)).default;
mermaid.initialize({ startOnLoad: false });

let checked = 0;
let bad = 0;
for (const file of process.argv.slice(2)) {
  for (const block of blocks(readFileSync(file, "utf8"))) {
    checked += 1;
    try {
      if (block.unclosed) throw new Error("the block has no closing fence");
      await mermaid.parse(block.code);
    } catch (error) {
      bad += 1;
      const message = String(error.message ?? error).split("\n").slice(0, 4).join("\n    ");
      console.log(`${file}:${block.line}: Mermaid cannot parse this block\n    ${message}`);
    }
  }
}
if (bad) console.log(`${bad} of ${checked} Mermaid blocks failed`);
process.exit(bad ? 1 : 0);
