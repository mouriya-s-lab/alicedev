import { build } from "esbuild";
import { chmod, copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");
const extensionDist = join(dist, "omp-extension");
const replyDist = join(dist, "reply-cli");

await rm(dist, { recursive: true, force: true });
await mkdir(extensionDist, { recursive: true });
await mkdir(replyDist, { recursive: true });

await build({
	entryPoints: [join(root, "omp-extension/index.ts")],
	outfile: join(extensionDist, "index.js"),
	bundle: true,
	platform: "node",
	format: "esm",
	external: ["@oh-my-pi/*"],
	logLevel: "info",
});

await writeFile(
	join(extensionDist, "package.json"),
	`${JSON.stringify({ name: "alicedev-omp-extension", type: "module", main: "index.js" })}\n`,
	"utf8",
);

const replySource = join(root, "reply-cli/index.mjs");
const replyTarget = join(replyDist, "alicedev-reply");
await copyFile(replySource, replyTarget);
await chmod(replyTarget, 0o755);
