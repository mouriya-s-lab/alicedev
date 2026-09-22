// Bundle harness/core to dist/test/core.mjs so node:test can import the TS sources.
import { build } from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
await build({
	entryPoints: [join(root, "core/index.ts")],
	outfile: join(root, "dist/test/core.mjs"),
	bundle: true,
	platform: "node",
	format: "esm",
	logLevel: "warning",
});
