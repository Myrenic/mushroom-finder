/**
 * Mushroom Finder API client.
 *
 * The product question is "what is likely fruiting at this place this week?",
 * so the SPA is built around a place search and an expectation list. The map
 * is background context, not the interface.
 */

const API = "/api"

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
  /** Beschermd of Rode Lijst-soort. */
  sensitive: boolean
  image_url: string | null
  image_credit: string | null
  image_credit_url: string | null
  /** Records around the selected date in the selected year (0 for future). */
  period_records: number
  period_last_seen: string | null
}

export interface ExpectResult {
  lat: number
  lon: number
  radius_km: number
  date: string
  week: number
  month: number
  is_future: boolean
  known_total: number
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
  sensitive: boolean
  lat: number
  lon: number
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

export const fetchMeta = () => api<Meta>("/meta")

export const searchPlaces = (q: string) =>
  api<{ results: Place[] }>(`/search?q=${encodeURIComponent(q)}`)

export const fetchExpect = (
  lat: number,
  lon: number,
  radiusKm: number,
  date?: string
) =>
  api<ExpectResult>(
    `/expect?lat=${lat}&lon=${lon}&radius_km=${radiusKm}${date ? `&date=${date}` : ""}`
  )

export const fetchRecent = (days = 90) =>
  api<{ reports: RecentReport[] }>(`/recent?limit=1500&days=${days}`)

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
