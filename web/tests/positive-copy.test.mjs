import assert from "node:assert/strict"
import { execFileSync } from "node:child_process"
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs"
import { SourceMap } from "node:module"
import { isIP } from "node:net"
import { tmpdir } from "node:os"
import { basename, dirname, join, relative, resolve } from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const web = fileURLToPath(new URL("../", import.meta.url))
const repo = resolve(web, "..")
const src = join(web, "src")
const visibleAttributes = new Set(["alt", "aria-description", "aria-label", "aria-roledescription",
  "aria-valuetext", "children", "description", "detail", "heading", "hint", "label", "message",
  "mode", "placeholder", "subtitle", "summary", "text", "title"])
const implementationProps = new Set(["cache", "className", "class", "id", "maskImage", "refetchType"])
const technicalCalls = new Set(["addEventListener", "getAttribute", "matchMedia", "matches", "querySelector",
  "querySelectorAll", "removeEventListener", "includes", "startsWith", "endsWith", "cn", "cva"])
const classBindings = new Map([
  ["app/header-controls.tsx", "itemClass"],
  ["pages/about.tsx", "row"],
  ["pages/agents.tsx", "setupGrid"],
  ["components/ui/dropdown-menu.tsx", "itemClass"],
])
const comparison = new Set([ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.EqualsEqualsEqualsToken,
  ts.SyntaxKind.ExclamationEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken])
const forbidden = /[:;—]|\b(?:no|not|never|without|nothing|none|cannot|[a-z]+n['’ʼ]t)\b/giu

function prose(text) {
  if (/^curl -s -A 'SwB\/[\w.+!-]+' -X POST https?:\/\/\S+\/connect -H 'content-type: application\/json' -d '\{"code": "[\w-]+"\}'$/.test(text)) return ""
  if (text.startsWith("[") && text.endsWith("]") && isIP(text.slice(1, -1)) === 6) return ""
  return text
    .replace(/https?:\/\/[^\s<>"'`—]+/g, url => url.match(/[:;]+$/)?.[0] ?? "")
    .replace(/\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b/g, "")
    .replace(/\b(?:admin|agent|captain|key|pending|routine|thread):[\w.-]+(?::[\w.-]+)*\b/g, "")
    .replace(/`(?:[\w.-]+:|[\w.-]+(?:[.=][\w.-]+)+)`/g, "")
}

function jsxDecode(text) {
  const entities = { amp: "&", apos: "'", gt: ">", lt: "<", nbsp: " ", quot: '"', colon: ":", semi: ";", mdash: "—", rsquo: "’" }
  return text.replace(/&(#x[\da-f]+|#\d+|[a-z]+);/gi, (reference, entity) => {
    if (!entity.startsWith("#")) return entities[entity] ?? reference
    const code = entity[1].toLowerCase() === "x" ? Number.parseInt(entity.slice(2), 16) : Number(entity.slice(1))
    return code <= 0x10ffff ? String.fromCodePoint(code) : reference
  })
}

function authoredLiteral(node) {
  const path = relative(src, node.getSourceFile().fileName)
  for (let n = node; n.parent; n = n.parent) {
    const parent = n.parent
    if (ts.isTypeNode(parent) || ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) return false
    if (ts.isJsxAttribute(parent)) return visibleAttributes.has(parent.name.getText())
    if (ts.isJsxElement(parent) || ts.isJsxFragment(parent)) return true
    if (ts.isBinaryExpression(parent) && comparison.has(parent.operatorToken.kind)) return false
    if (ts.isCaseClause(parent) && parent.expression === n) return false
    if (ts.isElementAccessExpression(parent) && parent.argumentExpression === n) return false
    if (ts.isPropertyAssignment(parent) && (parent.name === n || implementationProps.has(parent.name.getText()))) return false
    if (ts.isVariableDeclaration(parent) && parent.name.getText() === classBindings.get(path)) return false
    if (ts.isFunctionDeclaration(parent) && path === "components/claim-link.tsx" && parent.name?.text === "duration" &&
        ts.isTemplateLiteralToken(node) && node.text === ":") return false
    if ((ts.isCallExpression(parent) || ts.isNewExpression(parent)) && parent.arguments?.includes(n)) {
      const callee = parent.expression
      if (ts.isPropertyAccessExpression(callee) && technicalCalls.has(callee.name.text)) return false
      if (ts.isIdentifier(callee) && technicalCalls.has(callee.text)) return false
      if (ts.isNewExpression(parent) && ts.isIdentifier(callee) && callee.text === "Error" && ts.isThrowStatement(parent.parent)) return false
    }
    if (ts.isConditionalExpression(parent) && parent.condition === n) return false
  }
  return true
}

function parseLiterals(path, accept, text = readFileSync(path, "utf8")) {
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true)
  const tokens = []
  function visit(node) {
    if ((ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node) || ts.isJsxText(node)) && accept(node)) {
      const inJsx = ts.isJsxText(node) || ts.isJsxAttribute(node.parent)
      const text = inJsx ? jsxDecode(node.text) : node.text
      tokens.push({ text, start: node.getStart(source), from: node.pos, end: node.end })
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return { source, tokens }
}

function* violations(entries) {
  for (const { text, path, line, origin } of entries) {
    const matches = [...new Set(prose(text.trim()).match(forbidden))]
    if (matches.length) yield `${origin}${relative(repo, path)}:${line} ${JSON.stringify(text.trim())} [${matches.join(", ")}]`
  }
}

function check(entries) {
  const findings = [...new Set(violations(entries))]
  assert.ok(findings.length === 0, findings.join("\n"))
}

function sourceEntries(file) {
  return file.tokens.map(({ text, start }) => ({
    text, path: file.source.fileName, line: file.source.getLineAndCharacterOfPosition(start).line + 1, origin: "",
  }))
}

const authored = new Map(readdirSync(src, { recursive: true }).filter(path => /\.tsx?$/.test(path))
  .map(path => [join(src, path), parseLiterals(join(src, path), authoredLiteral)]))

function builtEntries(output) {
  const entries = []
  const assets = join(output, "assets")
  for (const name of readdirSync(assets).filter(path => path.endsWith(".js.map"))) {
    const path = join(assets, name.slice(0, -4))
    const payload = JSON.parse(readFileSync(`${path}.map`, "utf8"))
    if (!payload.sources.some(source => authored.has(resolve(assets, decodeURI(source))))) continue
    const map = new SourceMap(payload)
    const generated = parseLiterals(path, () => true)
    for (const { text, start } of generated.tokens) {
      const { line, character } = generated.source.getLineAndCharacterOfPosition(start)
      const entry = map.findEntry(line, character)
      if (!entry.originalSource || entry.generatedLine !== line) continue
      const file = authored.get(resolve(assets, decodeURI(entry.originalSource)))
      if (!file) continue
      const offset = file.source.getPositionOfLineAndCharacter(entry.originalLine, entry.originalColumn)
      if (file.tokens.some(({ from, end }) => from <= offset && offset < end)) {
        entries.push({ text, path: file.source.fileName, line: entry.originalLine + 1, origin: `built ${basename(path)} ← ` })
      }
    }
  }
  return entries
}

test("authored React copy is positive", () => check([...authored.values()].flatMap(sourceEntries)))

test("the console setup code input offers one-time code autofill", () => {
  const login = readFileSync(join(src, "pages/login.tsx"), "utf8")
  const input = login.match(/<(?:input|Input)\b[^>]*\bname="code"[^>]*>/)
  assert.ok(input, "The setup form needs a code input")
  assert.match(input[0], /\bautoComplete="one-time-code"/)
})

test("minified UI copy is positive", () => {
  const output = mkdtempSync(join(tmpdir(), "swb-positive-copy-"))
  try {
    execFileSync(process.execPath, [join(web, "node_modules/vite/bin/vite.js"), "build", "--sourcemap", "--outDir", output], { cwd: web })
    const entries = builtEntries(output)
    assert.ok(entries.some(({ text, path }) => text.trim() && path.endsWith(".tsx")),
      "The production scan must find text from an authored JSX child")
    check(entries)
  } finally {
    rmSync(output, { force: true, recursive: true })
  }
})

test("Python claim and pairing copy is positive", () => {
  const python = process.env.COPY_TEST_PYTHON ?? join(repo, ".venv", "bin", "python")
  const data = execFileSync(python, [join(web, "tests", "positive_copy_pages.py")], { cwd: repo, encoding: "utf8" })
  check(JSON.parse(data).map(entry => ({ ...entry, path: join(repo, entry.path) })))
})

test("literal syntax and prohibited prose are distinct", () => {
  const matches = text => prose(text).match(forbidden)
  assert.equal(matches("Pair at 09:30 UTC with routine:example at https://swb.one/claim/no"), null)
  assert.equal(matches("Set `routes:` in the file"), null)
  assert.equal(matches("curl -s -A 'SwB/1.0.0' -X POST https://swb.one/connect -H 'content-type: application/json' -d '{\"code\": \"example\"}'"), null)
  assert.deepEqual(matches("Status:ready; this ISN’T active—never without it."), [":", ";", "ISN’T", "—", "never", "without"])
  assert.deepEqual(matches("Open https://swb.one/claim; then check."), [";"])
  assert.deepEqual(matches(jsxDecode("N&#111; status&mdash;check.")), ["No", "—"])
})

test("copy extraction reads JSX and code that supplies it", () => {
  const fixture = parseLiterals(join(src, "fixture.tsx"), authoredLiteral, `
    type Intake = "none"
    const labels = { none: "None configured." }
    const request = { cache: "no-store", id: "routine:fixture" }
    if (request.id === "no:copy") throw new Error("Not visible")
    const row = <p className="md:not-sr-only" aria-label="Status:ready">
      Never without access.
      {labels.none}
    </p>
  `)
  assert.deepEqual(sourceEntries(fixture).flatMap(({ text }) => {
    const tokens = prose(text).match(forbidden)
    return tokens ? [text.trim()] : []
  }), ["None configured.", "Status:ready", "Never without access."])
})
