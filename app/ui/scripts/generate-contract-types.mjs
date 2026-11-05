// Generates the TypeScript types of the UI from the JSON Schemas in spine_monitor/contracts/schemas/v1,
// which are the source of truth. Never edit src/contracts/v1.ts by hand: run `pnpm run gen:types`.
// `--check` fails when the committed file is stale (for CI).
//
// Every schema goes through one synthetic root, so a definition shared by several schemas
// (common.schema.json, the session inside a features request...) is declared once.
// $ref URLs (https://schemas.asclepius-orbit.example/v1/...) are resolved from the local files: nothing
// is downloaded. Conditional rules (if/then) are not expressible in TypeScript; the backend enforces them.

import { readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemasDir = path.resolve(here, "../../../spine_monitor/contracts/schemas/v1");
const output = path.resolve(here, "../src/contracts/v1.ts");
const baseUrl = "https://schemas.asclepius-orbit.example/v1/";

async function schemaFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((e) => (e.isDirectory() ? schemaFiles(path.join(dir, e.name)) : [path.join(dir, e.name)])),
  );
  return nested.flat().filter((f) => f.endsWith(".schema.json")).sort();
}

// A `description` next to a `$ref` makes the generator declare the referenced type again (Foo1);
// annotations beside a $ref add nothing to the type, so they are dropped.
function dropRefSiblings(node) {
  if (Array.isArray(node)) return node.map(dropRefSiblings);
  if (node === null || typeof node !== "object") return node;
  if (typeof node.$ref === "string") {
    const { description, title, examples, default: _default, ...rest } = node;
    if (Object.keys(rest).length === 1) return rest;
  }
  return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, dropRefSiblings(v)]));
}

const localResolver = {
  order: 1,
  canRead: (file) => file.url.startsWith(baseUrl),
  read: async (file) => {
    const text = await readFile(path.join(schemasDir, file.url.slice(baseUrl.length).split("#")[0]), "utf8");
    return JSON.stringify(dropRefSiblings(JSON.parse(text)));
  },
};

async function generate() {
  const files = await schemaFiles(schemasDir);
  const properties = {};
  for (const file of files) {
    const schema = JSON.parse(await readFile(file, "utf8"));
    if (!schema.title) continue; // common.schema.json only holds $defs
    const key = path.relative(schemasDir, file).replaceAll("\\", "/").replace(".schema.json", "");
    properties[key] = { $ref: schema.$id };
  }
  const root = {
    $id: `${baseUrl}__all__.schema.json`,
    title: "ContractsV1",
    description: "Index of every v1 contract, keyed by schema name. Only used to generate the types.",
    type: "object",
    properties,
    additionalProperties: false,
  };
  const banner = [
    "/* eslint-disable */",
    "/**",
    " * Generated from spine_monitor/contracts/schemas/v1 by scripts/generate-contract-types.mjs.",
    " * Do not edit by hand: run `pnpm run gen:types`.",
    " */",
  ].join("\n");
  return compile(root, "ContractsV1", {
    bannerComment: banner,
    cwd: schemasDir,
    declareExternallyReferenced: true,
    unreachableDefinitions: false,
    additionalProperties: false,
    strictIndexSignatures: true,
    $refOptions: { resolve: { local: localResolver, http: false, https: false, file: false } },
  });
}

const generated = await generate();
if (process.argv.includes("--check")) {
  const current = await readFile(output, "utf8").catch(() => "");
  if (current !== generated) {
    console.error(`${path.relative(process.cwd(), output)} is stale: run \`pnpm run gen:types\``);
    process.exit(1);
  }
  console.log("contract types are up to date");
} else {
  await writeFile(output, generated);
  console.log(`wrote ${path.relative(process.cwd(), output)}`);
}
