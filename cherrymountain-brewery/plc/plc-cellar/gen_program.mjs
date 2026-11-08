#!/usr/bin/env node
// Compile an ST file with the strucpp library API and write the three C++
// sources that the OpenPLC v4 runtime (strucpp based, v4.3.x) compiles:
// generated.cpp, generated.hpp, and generated_debug.cpp.
//
// The strucpp CLI does not write generated_debug.cpp, so this uses the library
// API. The IEC standard function-block and function libraries (TON, ...) come
// from the checkout's libs/ directory, as the CLI's -L option does.
//
// Usage: node gen_program.mjs STRUCPP_DIR PROGRAM.st OUT_DIR PROGRAM_MD5
//   STRUCPP_DIR  a built checkout of Autonomy-Logic/strucpp (npm ci && npm run build)
//   PROGRAM_MD5  lowercase hex MD5 of PROGRAM.st, recorded in the generated code
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [strucpp, stFile, outDir, md5] = process.argv.slice(2);
if (!strucpp || !stFile || !outDir || !/^[0-9a-f]{32}$/.test(md5 ?? "")) {
  console.error("usage: gen_program.mjs STRUCPP_DIR PROGRAM.st OUT_DIR PROGRAM_MD5");
  process.exit(2);
}
const entry = pathToFileURL(path.resolve(strucpp, "dist/node/index.js")).href;
const { compile, loadStlibFromFile } = await import(entry);
const libraries = ["iec-standard-fb.stlib", "iec-std-functions.stlib"].map((name) =>
  loadStlibFromFile(path.resolve(strucpp, "libs", name)),
);
const result = await compile(fs.readFileSync(stFile, "utf8"), { md5, libraries });
if (!result.success) {
  console.error(JSON.stringify(result.errors, null, 2));
  process.exit(1);
}
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "generated.cpp"), result.cppCode);
fs.writeFileSync(path.join(outDir, "generated.hpp"), result.headerCode);
fs.writeFileSync(path.join(outDir, "generated_debug.cpp"), result.debugTableCpp);
console.log(`wrote ${outDir}: ${result.debugMap.leaves.length} debug leaves`);
