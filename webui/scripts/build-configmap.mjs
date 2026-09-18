// Regenerates ../base/webui.configmap.json from the vite output in ../base/www.
//
// JS/CSS go into binaryData (base64): they contain raw control characters the
// YAML emitter refuses to write, and base64 is opaque to Flux postBuild
// substitution. index.html stays plain text so it is easy to inspect.
import { readdir, readFile, writeFile } from "node:fs/promises"
import { join, resolve } from "node:path"

const base = resolve(import.meta.dirname, "../../base")
const www = join(base, "www")
const out = join(base, "webui.configmap.json")

const files = (await readdir(www)).filter((f) => !f.startsWith("."))
const data = {}
const binaryData = {}

for (const f of files) {
  const content = await readFile(join(www, f))
  if (f === "index.html") {
    data[f] = content.toString("utf8")
  } else {
    binaryData[f] = content.toString("base64")
  }
}

const configMap = {
  apiVersion: "v1",
  kind: "ConfigMap",
  metadata: { name: "mushroom-webui" },
  ...(Object.keys(data).length ? { data } : {}),
  ...(Object.keys(binaryData).length ? { binaryData } : {}),
}

await writeFile(out, JSON.stringify(configMap, null, 2) + "\n")
const bytes = (await readFile(out)).byteLength
console.log(`wrote ${out} (${files.length} files, ${Math.round(bytes / 1024)} KiB)`)

// A ConfigMap object is capped around 1 MiB. Fail the build instead of
// silently shipping a bundle that Flux cannot apply.
if (bytes > 1024 * 1024) {
  console.error("ERROR: webui configmap exceeds the 1 MiB ConfigMap limit")
  process.exit(1)
}
