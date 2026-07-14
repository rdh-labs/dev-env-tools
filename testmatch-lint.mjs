#!/usr/bin/env node
/**
 * testmatch-lint — Adapter 1 of the QC-enforcement suite.
 *
 * Detects test-config fragmentation: a test file claimed by more than one jest
 * runner config with INCOMPATIBLE test environments (e.g. a DOM test globbed by
 * both a `jsdom` config and a `node` config — it silently runs under the wrong
 * environment in one of them). This is the ISSUE class behind
 * website-mq-studio's `tests/unit/homepage-hero.unit.test.tsx`.
 *
 * How it avoids reimplementing jest's matcher: for each config it asks jest
 * itself (`jest --listTests --config <cfg>`) which files that config claims —
 * ground truth that already honours testMatch AND testPathIgnorePatterns. The
 * config's `testEnvironment` is read by require()-ing the config object.
 *
 * Violations are handed to the shared `declare-fail` core (violations mode);
 * this adapter holds all the jest-specific logic, the core only exit-codes.
 *
 * Usage: node testmatch-lint.mjs --project <repoRoot> [--strict] [--json]
 *   --strict : also fail when a file is claimed by >1 config even if the
 *              environments agree (pure mutual-exclusion). Default: fail only
 *              on incompatible environments (the real, low-false-positive bug).
 * Exit: 0 clean · 1 violations · 2 tool/config error (propagated from core).
 */
import { execFileSync } from "node:child_process";
import { readdirSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const args = { project: process.cwd(), strict: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--project") args.project = argv[++i];
    else if (argv[i] === "--strict") args.strict = true;
    else if (argv[i] === "--json") args.json = true;
    else if (argv[i] === "--help") {
      console.log("Usage: testmatch-lint.mjs --project <repoRoot> [--strict] [--json]");
      process.exit(0);
    }
  }
  return args;
}

function findJestConfigs(root) {
  // Matches jest.config.cjs, jest.config.js, jest.config.unit.cjs, etc.
  return readdirSync(root)
    .filter((f) => /^jest\.config(\..+)?\.(c?js)$/.test(f))
    .sort();
}

/**
 * Files a jest config claims, repo-relative. Throws (does NOT swallow) if jest
 * itself errors — an unlistable config is a fail-loud signal, not "no tests".
 * An empty match is a normal exit-0 with no output and returns [].
 */
function listTests(root, configFile) {
  const out = execFileSync(
    "npx",
    ["jest", "--listTests", "--config", configFile],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }
  );
  return out
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((abs) => relative(root, abs));
}

/**
 * Build the file→configs claim map. Configs that cannot be analysed (require()
 * throws, function-exported so env is unreadable, or jest --listTests errors)
 * are collected in `problems` and surfaced later — never silently dropped.
 */
function collectClaims(root, configs) {
  const claims = new Map(); // repo-relative file -> [{ config, env }]
  const problems = []; // { config, reason }

  for (const cfgFile of configs) {
    let env;
    try {
      const cfg = require(join(root, cfgFile));
      if (typeof cfg === "function") {
        problems.push({
          config: cfgFile,
          reason: "config is exported as a function; testEnvironment cannot be read statically",
        });
        continue;
      }
      env = cfg.testEnvironment || "node";
    } catch (e) {
      problems.push({ config: cfgFile, reason: `config could not be required: ${e.message}` });
      continue;
    }

    let files;
    try {
      files = listTests(root, cfgFile);
    } catch (e) {
      problems.push({ config: cfgFile, reason: `jest --listTests failed: ${e.message.split("\n")[0]}` });
      continue;
    }

    for (const file of files) {
      if (!claims.has(file)) claims.set(file, []);
      claims.get(file).push({ config: cfgFile, env });
    }
  }
  return { claims, problems };
}

function computeViolations(claims, problems, strict) {
  const violations = [];

  // Fail loud on configs we could not analyse — unknown state is not clean.
  for (const p of problems) {
    violations.push({
      id: "config-unanalyzable",
      message: `jest config ${p.config} could not be analysed: ${p.reason}`,
      config: p.config,
    });
  }

  for (const [file, cs] of [...claims.entries()].sort()) {
    const envs = [...new Set(cs.map((c) => c.env))];
    const cfgNames = cs.map((c) => c.config);
    if (envs.length >= 2) {
      violations.push({
        id: "test-globbed-by-incompatible-envs",
        message: `${file} is claimed by ${cs.length} jest configs with incompatible test environments (${envs.join(", ")})`,
        file,
        configs: cfgNames,
        environments: envs,
      });
    } else if (strict && cs.length >= 2) {
      violations.push({
        id: "test-globbed-by-multiple-configs",
        message: `${file} is claimed by ${cs.length} jest configs (${cfgNames.join(", ")})`,
        file,
        configs: cfgNames,
        environments: envs,
      });
    }
  }
  return violations;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const configs = findJestConfigs(args.project);
  if (configs.length === 0) {
    // Fail loud: "no configs found" is unknown state, not "verified clean".
    console.error(
      `testmatch-lint: no jest configs found under ${args.project} — nothing inspected (check --project).`
    );
    process.exit(2);
  }
  const { claims, problems } = collectClaims(args.project, configs);
  const violations = computeViolations(claims, problems, args.strict);

  if (args.json) console.log(JSON.stringify(violations, null, 2));

  // Hand off to the shared core (violations mode). declare-fail is vendored
  // alongside this script (same dir), so CI runs without cross-repo access.
  // "violations" mirrors MODE_VIOLATIONS in lib/declare_fail.py — keep in sync.
  // mkdtempSync gives a private 0700 dir (no predictable-name TOCTOU).
  const tmpDir = mkdtempSync(join(tmpdir(), "testmatch-"));
  const tmp = join(tmpDir, "violations.json");
  writeFileSync(tmp, JSON.stringify(violations));
  const core = join(__dirname, "declare-fail");
  // Capture the exit code, clean up, THEN exit — process.exit() inside the
  // try/catch would bypass the finally and leak the temp dir.
  let code;
  try {
    execFileSync(
      "python3",
      [core, "--mode", "violations", "--violations", tmp, "--validate", "--label", "testMatch"],
      { stdio: "inherit" }
    );
    code = 0;
  } catch (e) {
    code = typeof e.status === "number" ? e.status : 2;
  } finally {
    try {
      rmSync(tmpDir, { recursive: true, force: true });
    } catch {
      /* best-effort cleanup */
    }
  }
  process.exit(code);
}

main();
