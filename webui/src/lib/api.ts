/**
 * Typed client for the Mushroom Finder API.
 *
 * The SPA only ever talks to /api/*; nginx proxies that to the in-pod API,
 * which is the only thing allowed to touch the database or the cluster.
 */

const API = "/api"

export interface Hotspot {
  cell_id: string
  guild: string
  guild_label: string
  lat: number
  lon: number
  static_score: number
  final_score: number
  condition_factor: number
  season_factor: number
  confidence: "high" | "medium" | "low"
  richness: number
  last_seen: string | null
  components: Record<string, number | string>
}

export interface Candidate {
  id: string
  guild: string
  guild_label: string
  lat: number
  lon: number
  fsp: number
  bucket: string
  confidence: string
  components: Record<string, number | string>
}

export interface FineCell {
  cell_id: string
  guild: string
  guild_label: string
  n: number
  years: number
  first_seen: string | null
  last_seen: string | null
  recent_n: number
  days_ago: number | null
  lat: number
  lon: number
  resolution_m: number
}

export interface Species {
  name_nl: string | null
  scientific_name: string
  guild: string
  photo_value: number
  n: number
  last_seen: string | null
}

export interface Weather {
  condition: number
  as_of: string | null
  precip_7d?: number
  precip_14d?: number
  precip_21d?: number
  dry_days?: number
  temp_c?: number
  soil_temp_c?: number
  soil_moisture?: number
  forecast_precip_3d?: number
}

export interface RefreshRun {
  id: string
  kind: string
  status: string
  progress: number
  phase: string | null
  message: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  stats?: Record<string, unknown> | null
}

export interface DatasetInfo {
  id: string
  provider: string
  title: string
  source_url: string
  licence: string
  attribution: string
  last_success_at: string | null
}

export interface Attribution {
  id: string
  label: string
  url: string
  licence: string
}

export interface Meta {
  model_version: string
  datasets: DatasetInfo[]
  layers: Array<{ layer: string; version: string; resolution_m: number | null }>
  runs: RefreshRun[]
  attributions: Attribution[]
  weather: Weather
}

export interface Me {
  email: string
  user: string
  groups: string[]
  authenticated: boolean
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.error ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const fetchMe = () => api<Me>("/me")
export const fetchMeta = () => api<Meta>("/meta")
export const fetchWeather = () => api<Weather>("/weather")
export const fetchHotspots = (guild?: string, limit = 800) =>
  api<{ hotspots: Hotspot[]; guilds: Record<string, string> }>(
    `/hotspots?limit=${limit}${guild ? `&guild=${encodeURIComponent(guild)}` : ""}`
  )
export const fetchSpecies = (cellId: string) =>
  api<{ cell_id: string; species: Species[] }>(
    `/hotspots/${encodeURIComponent(cellId)}`
  )
export const fetchFineCells = (guild?: string, days = 0) =>
  api<{ cells: FineCell[] }>(
    `/fine-cells?limit=2000${guild ? `&guild=${encodeURIComponent(guild)}` : ""}${
      days ? `&days=${days}` : ""
    }`
  )
export const fetchCandidates = (guild?: string, limit = 400) =>
  api<{ candidates: Candidate[] }>(
    `/candidates?limit=${limit}${guild ? `&guild=${encodeURIComponent(guild)}` : ""}`
  )
export const fetchRefreshRuns = () => api<{ runs: RefreshRun[] }>("/refresh")

export const startRefresh = (kind: "weather" | "historical") =>
  api<{ run_id: string; kind: string }>("/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind }),
  })

export const analyseArea = (
  bbox: { south: number; west: number; north: number; east: number },
  guild: string
) =>
  api<{ run_id: string; kind: string }>("/aoi", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bbox_wgs84: bbox, guild }),
  })

export const cancelRun = (runId: string) =>
  api<{ ok: boolean }>(`/refresh/${encodeURIComponent(runId)}`, { method: "DELETE" })
