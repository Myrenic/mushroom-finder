/**
 * Mushroom Finder API client.
 *
 * The product question is "what is likely fruiting at this place this week?",
 * so the SPA is built around a place search and an expectation list. The map
 * is background context, not the interface.
 */

const API = "/api"

export interface Me {
  email: string
  user: string
  groups: string[]
  authenticated: boolean
}

export interface Place {
  id: string
  name: string
  type: string
  lat: number
  lon: number
  municipality?: string | null
  province?: string | null
  source: string
}

export interface Weather {
  condition?: number
  as_of?: string | null
  precip_7d?: number
  precip_14d?: number
  precip_21d?: number
  dry_days?: number
  temp_c?: number
  soil_temp_c?: number
  soil_moisture?: number
  forecast_precip_3d?: number
}

export interface ExpectSpecies {
  species_id: string
  name_nl: string | null
  scientific_name: string
  guild: string
  guild_label: string
  photo_value: number
  local_records: number
  last_seen: string | null
  days_ago: number | null
  seasonal: number
  peak_week: number
  expected: number
  confidence: number
  phase: string
  reasons: string[]
}

export interface ExpectResult {
  lat: number
  lon: number
  radius_km: number
  week: number
  month: number
  weather: Weather
  species: ExpectSpecies[]
}

export interface RecentReport {
  id: string
  guild: string
  guild_label: string
  name_nl: string | null
  scientific_name: string
  observed_on: string
  days_ago: number
  resolution_m: number
  precise: boolean
  lat: number
  lon: number
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
}

export interface Meta {
  model_version: string
  datasets: Array<{
    id: string
    provider: string
    title: string
    source_url: string
    licence: string
    last_success_at: string | null
  }>
  runs: RefreshRun[]
  attributions: Array<{ id: string; label: string; url: string; licence: string }>
  weather: Weather
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

export const searchPlaces = (q: string) =>
  api<{ results: Place[] }>(`/search?q=${encodeURIComponent(q)}`)

export const fetchExpect = (lat: number, lon: number, radiusKm: number) =>
  api<ExpectResult>(`/expect?lat=${lat}&lon=${lon}&radius_km=${radiusKm}`)

export const fetchRecent = (days = 90) =>
  api<{ reports: RecentReport[] }>(`/recent?limit=1500&days=${days}`)

export const startRefresh = (kind: "weather" | "historical") =>
  api<{ run_id: string; kind: string }>("/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind }),
  })

export const cancelRun = (runId: string) =>
  api<{ ok: boolean }>(`/refresh/${encodeURIComponent(runId)}`, { method: "DELETE" })

/** Great-circle distance in km, for filtering nearby reports client-side. */
export function distanceKm(
  aLat: number,
  aLon: number,
  bLat: number,
  bLon: number
): number {
  const R = 6371
  const dLat = ((bLat - aLat) * Math.PI) / 180
  const dLon = ((bLon - aLon) * Math.PI) / 180
  const la1 = (aLat * Math.PI) / 180
  const la2 = (bLat * Math.PI) / 180
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}
