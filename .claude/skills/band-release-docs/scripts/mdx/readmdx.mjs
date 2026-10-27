/**
 * Reads Fern docs pages the way Fern renders them, for checkpatch.py.
 *
 * stdin: a JSON list of page texts. stdout: a JSON list with, per page, the
 * link targets, JSX tags, expressions, and imports MDX renders, or null when
 * the page is not MDX. Nothing from a page reaches stderr: pages are private.
 *
 * Usage: node readmdx.mjs < pages.json
 */

import { text } from "node:stream/consumers";
import remarkGfm from "remark-gfm";
import remarkMdx from "remark-mdx";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { visit } from "unist-util-visit";

const parser = unified().use(remarkParse).use(remarkMdx).use(remarkGfm);
const TARGETS = new Set(["link", "image", "definition"]);
const TAGS = new Set(["mdxJsxFlowElement", "mdxJsxTextElement"]);
const EXPRESSIONS = new Set(["mdxFlowExpression", "mdxTextExpression"]);

/** A tag's name and attributes: an edit inside its children leaves it unchanged. */
function tag(node) {
  const attributes = node.attributes.map(({ name, value }) => [
    name ?? null,
    typeof value === "object" && value !== null ? { expression: value.value } : value,
  ]);
  return JSON.stringify([node.name, attributes]);
}

function read(page) {
  let tree;
  try {
    tree = parser.runSync(parser.parse(page));
  } catch {
    return null;
  }
  const found = { targets: [], tags: [], expressions: [], imports: [] };
  visit(tree, node => {
    if (TARGETS.has(node.type)) found.targets.push(node.url);
    if (TAGS.has(node.type)) found.tags.push(tag(node));
    if (EXPRESSIONS.has(node.type)) found.expressions.push(node.value);
    if (node.type === "mdxjsEsm") found.imports.push(node.value);
  });
  return found;
}

const pages = JSON.parse(await text(process.stdin));
process.stdout.write(JSON.stringify(pages.map(read)));
