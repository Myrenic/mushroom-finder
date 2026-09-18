// Packages the API, worker and migration sources into ConfigMaps under base/.
//
// Why committed JSON instead of configMapGenerator file refs: kustomize
// refuses file sources outside the kustomization directory, and the sources
// deliberately live at the app root (api/, worker/, migrations/) next to the
// webui and docs. This mirrors the chacdn bundle convention.
//
// Run after changing api/, worker/ or migrations/:
//   node scripts/build-worker-configmaps.mjs
import { readdir, readFile, writeFile } from "node:fs/promises"
import { join, resolve } from "node:path"

const app = resolve(import.meta.dirname, "..")
const base = join(app, "base")

async function writeConfigMap(name, outName, files) {
  const data = {}
  for (const [key, path] of files) {
    data[key] = await readFile(path, "utf8")
  }
  const configMap = {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: { name },
    data,
  }
  // Flux postBuild substitution mangles unknown ${...} sequences in plain
  // ConfigMap data (this bit the chacdn idle-culler script). Fail loudly.
  for (const [key, value] of Object.entries(data)) {
    if (value.includes("${")) {
      console.error(
        "ERROR: " + key + " contains a ${ sequence which Flux would substitute"
      )
      process.exit(1)
    }
  }
  const out = join(base, outName)
  await writeFile(out, JSON.stringify(configMap, null, 2) + "\n")
  const bytes = (await readFile(out)).byteLength
  console.log(`wrote ${out} (${files.length} files, ${Math.round(bytes / 1024)} KiB)`)
  return bytes
}

const api = await writeConfigMap("mushroom-api", "api.configmap.json", [
  ["server.py", join(app, "api", "server.py")],
])

const workerFiles = (await readdir(join(app, "worker")))
  .filter((f) => f.endsWith(".py"))
  .sort()
  .map((f) => [f, join(app, "worker", f)])
const worker = await writeConfigMap("mushroom-worker", "worker.configmap.json", workerFiles)

const migrationFiles = (await readdir(join(app, "migrations")))
  .filter((f) => f.endsWith(".sql"))
  .sort()
  .map((f) => [f, join(app, "migrations", f)])
const migrations = await writeConfigMap(
  "mushroom-migrations",
  "migrations.configmap.json",
  migrationFiles
)

if (api + worker + migrations > 1024 * 1024) {
  console.error("ERROR: combined configmaps exceed the 1 MiB ConfigMap limit")
  process.exit(1)
}
