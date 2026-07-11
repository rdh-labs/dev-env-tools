#!/usr/bin/env node
/**
 * payload-schema-lint — deterministic Payload CMS schema check
 * (IDEA-10495 / ISSUE-3466; reflexion:critique deterministic lens)
 *
 * WHY: reflexion:critique's judge panel is same-model LLMs with no mechanical lens.
 * Two schema defects shipped to a live client for ~5 months undetected (ISSUE-3466):
 * a `required` field with no human-facing label/description (shows only Payload's
 * auto-titleized field name), and a dead field never consumed downstream. This tool
 * is the external/deterministic signal the trust-spiral keystone (Dart O7t4WAplaNNk)
 * calls for — NOT a 5th same-model judge.
 *
 * PHASE-1 RULE (shipped, ~0 FP): every field with `required: true` MUST carry an
 * explicit `label` OR `admin.description`. Verified: 0 findings / 0 FP across the
 * 22 required fields of MQ Studio at HEAD; flags the pre-fix `Media.alt` defect.
 *
 * MECHANISM: AST parse via the target project's own `typescript` — env-free. (Importing
 * payload.config.ts directly is NOT viable: it runs validateEnv() at import time.)
 *
 * ADVISORY / FAIL-OPEN: exit 0 always, unless --strict (then exit 1 on findings).
 * Exit 2 on an UNEXPECTED internal error — exit 1 is reserved strictly for genuine
 * findings so a commit-hook consumer can fail OPEN on a tool bug (treat >1 as skip),
 * not falsely block (ISSUE-3466 /ship review, 2026-07-11).
 *
 * USAGE:
 *   node payload-schema-lint.mjs --project <projectDir> [files...] [--json] [--strict]
 *   (no files -> scans <projectDir>/src/{collections,globals,fields})
 *
 * Consumed by reflexion:critique when a diff touches Payload schema files. When a 2nd
 * Payload project appears, this stays project-agnostic via --project (no change needed).
 *
 * LIMITATIONS (false-negative surface — a "clean" result is NOT proof of no defects):
 * static AST-of-source only. A field whose `name` is dynamic is reported as `<dynamic:...>`,
 * but fields injected via SPREAD (`...sharedField`), returned from a FACTORY call, or IMPORTED
 * from another module are not followed and can be missed. Scanning src/fields mitigates but does
 * not close this. Advisory by design — do not treat a pass as exhaustive.
 */
import { createRequire } from 'module'
import fs from 'fs'
import path from 'path'

function parseArgs(argv) {
  const o = { project: process.cwd(), files: [], json: false, strict: false }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--project') o.project = path.resolve(argv[++i])
    else if (a === '--json') o.json = true
    else if (a === '--strict') o.strict = true
    else o.files.push(a)
  }
  return o
}

const args = parseArgs(process.argv.slice(2))

// Resolve TypeScript from the target project (env-free, no version drift).
let ts
try {
  const req = createRequire(path.join(args.project, 'package.json'))
  ts = req('typescript')
} catch (e) {
  console.error(`payload-schema-lint: could not load 'typescript' from ${args.project}/node_modules — ${e.message}`)
  process.exit(0) // fail-open: never break a review because tooling is missing
}

function discoverFiles(project) {
  const dirs = [
    path.join(project, 'src', 'collections'),
    path.join(project, 'src', 'globals'),
    path.join(project, 'src', 'fields'), // reusable field factories/definitions
  ]
  const out = []
  for (const d of dirs) {
    if (!fs.existsSync(d)) continue
    for (const f of fs.readdirSync(d)) {
      if (f.endsWith('.ts') && !f.endsWith('.d.ts')) out.push(path.join(d, f))
    }
  }
  return out
}

// Map an object-literal node's property keys -> value nodes (PropertyAssignment only).
function propMap(objNode) {
  const m = {}
  for (const p of objNode.properties) {
    if (ts.isPropertyAssignment(p) && (ts.isIdentifier(p.name) || ts.isStringLiteralLike(p.name))) {
      m[p.name.text] = p.initializer
    } else if (ts.isShorthandPropertyAssignment(p)) {
      // `{ name, type, required }` — value is a same-named identifier (dynamic). Record the
      // identifier node so a shorthand `name` still registers the object as a field (else the
      // field is silently skipped — a false negative, the worst failure mode for a defect check).
      m[p.name.text] = p.name
    }
  }
  return m
}

// A label/description "explains" a field only if present AND not an explicit empty/falsy
// literal. Present-but-empty (`label: ''`, `admin.description: false/null/undefined`) must
// still count as unexplained. Non-literal values (localized `{en:...}` objects, functions,
// identifiers) are treated as meaningful — presence is enough; don't false-positive on them.
function isMeaningful(node) {
  if (!node) return false
  if (ts.isStringLiteralLike(node)) return node.text.trim().length > 0
  if (node.kind === ts.SyntaxKind.FalseKeyword || node.kind === ts.SyntaxKind.NullKeyword) return false
  if (ts.isIdentifier(node) && node.text === 'undefined') return false
  return true
}

function lintFile(file) {
  const src = fs.readFileSync(file, 'utf8')
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, /*setParentNodes*/ true)
  const findings = []
  const requiredTable = [] // every required field + how it's explained (for human eyeball)

  function visit(node) {
    if (ts.isObjectLiteralExpression(node)) {
      const m = propMap(node)
      const nameNode = m['name']
      const typeNode = m['type']
      // A Payload field literal has a `name` (string or shorthand identifier) AND a string-literal
      // `type`. Requiring a string-literal `type` excludes imageSizes {name,width,...} and admin blocks.
      const isField =
        nameNode &&
        typeNode &&
        ts.isStringLiteralLike(typeNode) &&
        (ts.isStringLiteralLike(nameNode) || ts.isIdentifier(nameNode))
      if (isField && m['required'] && m['required'].kind === ts.SyntaxKind.TrueKeyword) {
        const fieldName = ts.isStringLiteralLike(nameNode) ? nameNode.text : `<dynamic:${nameNode.text}>`
        const hasLabel = isMeaningful(m['label'])
        const adminObj = m['admin'] && ts.isObjectLiteralExpression(m['admin']) ? propMap(m['admin']) : null
        const hasAdminDesc = !!(adminObj && isMeaningful(adminObj['description']))
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1
        requiredTable.push({
          field: fieldName,
          type: typeNode.text,
          line,
          explanation: hasLabel ? 'label' : hasAdminDesc ? 'admin.description' : null,
        })
        if (!hasLabel && !hasAdminDesc) {
          findings.push({
            field: fieldName,
            type: typeNode.text,
            line,
            rule: 'required-field-needs-label-or-description',
            message: `required field '${fieldName}' has no label and no admin.description — Payload shows only the auto-titleized name with no guidance`,
          })
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return { findings, requiredTable }
}

let allFindings = []
let allRequired = []
try {
  const files = args.files.length ? args.files.map((f) => path.resolve(f)) : discoverFiles(args.project)
  for (const f of files) {
    try {
      const rel = path.relative(args.project, f)
      const r = lintFile(f)
      allFindings = allFindings.concat(r.findings.map((x) => ({ file: rel, ...x })))
      allRequired = allRequired.concat(r.requiredTable.map((x) => ({ file: rel, ...x })))
    } catch (e) {
      console.error(`payload-schema-lint: failed to parse ${f}: ${e.message}`)
    }
  }

  if (args.json) {
    console.log(JSON.stringify({ findings: allFindings, requiredFieldCount: allRequired.length, requiredFieldTable: allRequired }, null, 2))
  } else {
    console.log(`payload-schema-lint: scanned ${files.length} file(s), ${allRequired.length} required field(s)`)
    if (allFindings.length === 0) {
      console.log('  ✓ no required-field-without-explanation findings')
    } else {
      console.log(`  ✗ ${allFindings.length} finding(s):`)
      for (const f of allFindings) console.log(`    ${f.file}:${f.line}  ${f.field} (${f.type}) — ${f.message}`)
    }
  }
} catch (e) {
  // Unexpected error (file discovery, output). Reserve exit 1 for genuine findings so a
  // commit-hook consumer fails OPEN on a tool bug (exit 2 → skip), not falsely block.
  console.error(`payload-schema-lint: unexpected internal error — ${e && e.stack ? e.stack : e}`)
  process.exit(2)
}

process.exit(args.strict && allFindings.length ? 1 : 0)
