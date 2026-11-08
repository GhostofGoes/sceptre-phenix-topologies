#!/usr/bin/env node
// Compile an ST file with the strucpp library API and write the three sources the
// OpenPLC v4 runtime needs (generated.cpp, generated.hpp, generated_debug.cpp)
// plus debug_map.json (variable path -> arr/elem, used in conf/opcua.json).
// The strucpp CLI (-d) does NOT emit generated_debug.cpp; the library API does.
// Usage: node gen_program.mjs STRUCPP_DIR prog.st OUT_DIR
//   STRUCPP_DIR: a built checkout of Autonomy-Logic/strucpp (npm ci && npm run build)
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [strucpp, stFile, outDir] = process.argv.slice(2);
if (!strucpp || !stFile || !outDir) {
  console.error("usage: gen_program.mjs STRUCPP_DIR prog.st OUT_DIR");
  process.exit(2);
}
const { compile } = await import(pathToFileURL(path.resolve(strucpp, "dist/index.js")).href);
const result = await compile(fs.readFileSync(stFile, "utf8"), { md5: "spike" });
if (!result.success) {
  console.error(JSON.stringify(result.errors, null, 2));
  process.exit(1);
}
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "generated.cpp"), result.cppCode);
fs.writeFileSync(path.join(outDir, "generated.hpp"), result.headerCode);
fs.writeFileSync(path.join(outDir, "generated_debug.cpp"), result.debugTableCpp);
fs.writeFileSync(path.join(outDir, "debug_map.json"), JSON.stringify(result.debugMap, null, 1));
console.log(`wrote ${outDir}: ${result.debugMap.leaves.length} debug leaves`);
