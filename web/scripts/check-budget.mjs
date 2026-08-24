#!/usr/bin/env node
// Assert the JS/CSS budget from docs/performance-budget.md against a real
// build. `npm run build && npm run budget`.
//
// The budget lives in a document; this makes it fail loudly instead of aging
// into a paragraph nobody re-measures. The server-rendered half has the
// equivalent assertion in tests/web/test_render.py::TestPerformanceBudget.

import { gzipSync } from 'node:zlib'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist')

// Gzipped bytes. Brotli would be smaller, but gzip is what every proxy and
// low-end Android in this deployment context is guaranteed to negotiate, so
// the budget is stated against the worst case we actually expect to serve.
const BUDGETS = {
  js: 100 * 1024,
  css: 20 * 1024,
}

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

let files
try {
  files = walk(DIST)
} catch {
  console.error('No dist/ — run `npm run build` first.')
  process.exit(2)
}

const totals = { js: 0, css: 0 }
const rows = []
for (const file of files) {
  const ext = file.endsWith('.js') ? 'js' : file.endsWith('.css') ? 'css' : null
  if (!ext) continue
  const size = gzipSync(readFileSync(file), { level: 9 }).length
  totals[ext] += size
  rows.push([file.slice(DIST.length + 1), ext, size])
}

rows.sort((a, b) => b[2] - a[2])
for (const [name, ext, size] of rows) {
  console.log(`  ${String(size).padStart(7)} B gz  ${ext.padEnd(3)}  ${name}`)
}

let failed = false
for (const [ext, budget] of Object.entries(BUDGETS)) {
  const used = totals[ext]
  const pct = ((used / budget) * 100).toFixed(0)
  const verdict = used <= budget ? 'OK  ' : 'OVER'
  console.log(`${verdict} ${ext.toUpperCase()}: ${used} B gzipped of ${budget} B budget (${pct}%)`)
  if (used > budget) failed = true
}

if (failed) {
  console.error('\nBudget exceeded. Either shrink the bundle or change the budget in ' +
    'docs/performance-budget.md deliberately — do not raise it here alone.')
  process.exit(1)
}
