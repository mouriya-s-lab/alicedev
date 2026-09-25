import { build } from "esbuild";
import { chmod, copyFile, cp, mkdir, rm, stat, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");
const extensionDist = join(dist, "omp-extension");
const cliDist = join(dist, "cli");

await rm(dist, { recursive: true, force: true });
await mkdir(extensionDist, { recursive: true });
await mkdir(cliDist, { recursive: true });

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

const cliSource = join(root, "cli/index.mjs");
const cliTarget = join(cliDist, "alicedev");
await copyFile(cliSource, cliTarget);
await chmod(cliTarget, 0o755);

const skillsSource = join(root, "skills");
let skillsStats;
try {
	skillsStats = await stat(skillsSource);
} catch (error) {
	if (error.code !== "ENOENT") throw error;
}
if (skillsStats !== undefined) {
	if (!skillsStats.isDirectory()) throw new Error("harness/skills must be a directory");
	await cp(skillsSource, join(extensionDist, "skills"), { recursive: true });
}
