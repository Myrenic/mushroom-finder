import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  CloudRain,
  Database,
  Loader2,
  MapPin,
  Moon,
  Search,
  Sun,
} from "lucide-react"

import { Badge } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { MushroomIcon } from "@/components/MushroomIcon"
import { MapView } from "@/components/MapView"
import {
  cancelRun,
  distanceKm,
  fetchExpect,
  fetchMe,
  fetchMeta,
  fetchRecent,
  searchPlaces,
  startRefresh,
  type ExpectResult,
  type Me,
  type Meta,
  type Place,
  type RecentReport,
  type Weather,
} from "@/lib/api"

const EXAMPLES = ["'t Nije Hemelriek", "Veluwe", "Gasselte", "Assen"]

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(
    () => (localStorage.getItem("mf-theme") as "light" | "dark") || "dark"
  )
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    localStorage.setItem("mf-theme", theme)
  }, [theme])
  return { theme, setTheme }
}

function todayIso(): string {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10)
}

function addDays(iso: string, days: number): string {
  const d = new Date(iso + "T12:00:00")
  d.setDate(d.getDate() + days)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10)
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "nooit"
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return "net"
  if (mins < 60) return `${mins} min`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} u`
  return `${Math.round(hours / 24)} d`
}

// Geocoders leveren een mix van Nederlandse en Engelse types.
const TYPE_NL: Record<string, string> = {
  water: "water",
  protected_area: "natuurgebied",
  nature_reserve: "natuurgebied",
  forest: "bos",
  heath: "heide",
  park: "park",
  administrative: "gebied",
  municipality: "gemeente",
  village: "dorp",
  hamlet: "buurtschap",
  city: "stad",
  town: "plaats",
  weg: "weg",
  adres: "adres",
  perceel: "perceel",
  postcode: "postcode",
  woonplaats: "woonplaats",
  gemeente: "gemeente",
  provincie: "provincie",
}
const typeNl = (t: string) => TYPE_NL[(t || "").toLowerCase()] ?? t

function phaseTone(seasonal: number): string {
  if (seasonal >= 0.7) return "bg-primary/15 text-primary"
  if (seasonal >= 0.35) return "bg-accent/50 text-accent-foreground"
  return "bg-muted text-muted-foreground"
}

export function App() {
  const { theme, setTheme } = useTheme()
  const [me, setMe] = useState<Me | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  // search
  const [query, setQuery] = useState("")
  const [suggestions, setSuggestions] = useState<Place[]>([])
  const [searching, setSearching] = useState(false)
  const [open, setOpen] = useState(false)

  // selected place + results
  const [place, setPlace] = useState<Place | null>(null)
  const [radiusKm, setRadiusKm] = useState(5)
  // Which date's season we are projecting. Past dates also show what was
  // actually reported around then.
  const [viewDate, setViewDate] = useState<string>(() => todayIso())
  const [expect, setExpect] = useState<ExpectResult | null>(null)
  const [expectLoading, setExpectLoading] = useState(false)
  const [recent, setRecent] = useState<RecentReport[]>([])

  const boxRef = useRef<HTMLDivElement>(null)

  const loadRuns = useCallback(async () => {
    try {
      setMeta(await fetchMeta())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        setMe(await fetchMe())
      } catch {
        /* read-only browsing is fine without identity */
      }
      await loadRuns()
      try {
        setRecent((await fetchRecent(90)).reports)
      } catch {
        /* recent is supplementary */
      }
    })()
  }, [loadRuns])

  // Poll while a refresh runs so progress and data stay live.
  useEffect(() => {
    const active = meta?.runs.some((r) => r.status === "running" || r.status === "queued")
    if (!active) return
    const t = setInterval(loadRuns, 5000)
    return () => clearInterval(t)
  }, [meta, loadRuns])

  // Debounced place search.
  useEffect(() => {
    const q = query.trim()
    if (q.length < 3) {
      setSuggestions([])
      return
    }
    let cancelled = false
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const r = await searchPlaces(q)
        if (!cancelled) {
          setSuggestions(r.results)
          setOpen(true)
        }
      } catch {
        if (!cancelled) setSuggestions([])
      } finally {
        if (!cancelled) setSearching(false)
      }
    }, 350)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
  }, [query])

  // Close the suggestion list on outside click.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDown)
    return () => document.removeEventListener("mousedown", onDown)
  }, [])

  const loadExpect = useCallback(
    async (p: Place, radius: number, date: string) => {
      setExpectLoading(true)
      setError(null)
      try {
        setExpect(await fetchExpect(p.lat, p.lon, radius, date))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
        setExpect(null)
      } finally {
        setExpectLoading(false)
      }
    },
    []
  )

  const choose = (p: Place) => {
    setPlace(p)
    setOpen(false)
    setQuery("")
    setSuggestions([])
    loadExpect(p, radiusKm, viewDate)
  }

  useEffect(() => {
    if (place) loadExpect(place, radiusKm, viewDate)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [radiusKm, viewDate])

  const recentNearby = useMemo(() => {
    if (!place) return []
    // Records are snapped to their cell centre, so allow a margin over the
    // analysis radius before calling something "nearby".
    const limit = radiusKm + 3
    return recent
      .filter((r) => distanceKm(place.lat, place.lon, r.lat, r.lon) <= limit)
      .sort((a, b) => a.days_ago - b.days_ago)
  }, [place, radiusKm, recent])

  const trigger = async (kind: "weather" | "historical") => {
    setBusy(kind)
    try {
      await startRefresh(kind)
      await loadRuns()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const activeRun = meta?.runs.find((r) => r.status === "running" || r.status === "queued")

  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card/80 px-3 backdrop-blur">
        <button
          type="button"
          onClick={() => {
            setPlace(null)
            setExpect(null)
          }}
          className="flex shrink-0 items-center gap-2"
          title="Opnieuw beginnen"
        >
          <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
            <MushroomIcon className="size-4" />
          </span>
          <span className="hidden text-sm font-semibold sm:inline">Paddenstoelenzoeker</span>
        </button>

        <div ref={boxRef} className="relative min-w-0 flex-1 md:max-w-xl">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onFocus={() => suggestions.length && setOpen(true)}
            placeholder="Zoek een plek, bijvoorbeeld Hemelriek of Gasselte"
            className="h-9 w-full rounded-lg border border-border bg-background pl-9 pr-8 text-sm"
          />
          {searching && (
            <Loader2 className="absolute top-1/2 right-3 size-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          )}
          {open && suggestions.length > 0 && (
            <div className="absolute top-10 left-0 z-[600] max-h-80 w-full overflow-y-auto rounded-lg border bg-popover py-1 shadow-lg">
              {suggestions.map((s) => (
                <button
                  key={s.id}
                  onClick={() => choose(s)}
                  className="flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  <MapPin className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block truncate">{s.name}</span>
                    <span className="block text-[11px] text-muted-foreground">
                      {[typeNl(s.type), s.municipality, s.province].filter(Boolean).join(" · ")}
                      {" · "}
                      {s.source}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            onClick={() => trigger("weather")}
            disabled={busy === "weather" || !!activeRun}
            title="Weer bijwerken"
          >
            {busy === "weather" ? <Loader2 className="animate-spin" /> : <CloudRain />}
            <span className="hidden lg:inline">Weer</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => trigger("historical")}
            disabled={busy === "historical" || !!activeRun}
            title="Waarnemingen opnieuw inladen"
          >
            {busy === "historical" ? <Loader2 className="animate-spin" /> : <Database />}
            <span className="hidden lg:inline">Historie</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title="Licht of donker"
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
      </header>

      {activeRun && (
        <div className="flex items-center gap-2 border-b bg-accent/30 px-3 py-1.5 text-xs">
          <Loader2 className="size-3.5 animate-spin" />
          <span className="font-medium">{activeRun.kind === "weather" ? "Weer" : "Historie"}</span>
          <span className="text-muted-foreground">
            {activeRun.phase} · {Math.round((activeRun.progress ?? 0) * 100)}%
            {activeRun.message ? ` · ${activeRun.message}` : ""}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={async () => {
              await cancelRun(activeRun.id)
              await loadRuns()
            }}
          >
            Stop
          </Button>
        </div>
      )}

      {error && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <main className="min-h-0 flex-1 overflow-y-auto">
        {!place ? (
          <Welcome onExample={(q) => setQuery(q)} />
        ) : (
          <div className="mx-auto max-w-6xl px-4 py-5">
            <PlaceHeader
              place={place}
              radiusKm={radiusKm}
              onRadius={setRadiusKm}
              viewDate={viewDate}
              onDate={setViewDate}
              weather={expect?.weather}
              result={expect}
            />

            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_380px]">
              <section>
                {expectLoading ? (
                  <div className="grid place-items-center py-20 text-sm text-muted-foreground">
                    <Loader2 className="mb-2 size-5 animate-spin" />
                    Bezig met opzoeken wat hier bekend is…
                  </div>
                ) : !expect || expect.species.length === 0 ? (
                  <Card>
                    <CardContent className="py-8 text-center text-sm text-muted-foreground">
                      {expect && expect.known_total > 0
                        ? `${expect.known_total} soorten zijn hier bekend, maar rond ${viewDate} (week ${expect.week}) is daarvan niets op z'n best. Probeer een andere datum.`
                        : `Binnen ${radiusKm} km van deze plek staat nog niets in de lijst. Probeer een grotere straal.`}
                    </CardContent>
                  </Card>
                ) : (
                  <LikelyList data={expect} />
                )}
              </section>

              <aside className="space-y-4">
                <Card className="overflow-hidden">
                  <div className="h-64">
                    <MapView
                      center={{ lat: place.lat, lon: place.lon }}
                      radiusKm={radiusKm}
                      reports={recentNearby}
                    />
                  </div>
                </Card>
                <RecentCard reports={recentNearby} radiusKm={radiusKm} />
              </aside>
            </div>

            <footer className="mt-6 border-t pt-3 text-[11px] text-muted-foreground">
              {meta?.attributions.map((a) => (
                <span key={a.id} className="mr-3 inline-block">
                  <a className="underline" href={a.url} target="_blank" rel="noreferrer">
                    {a.label}
                  </a>
                </span>
              ))}
              <div className="mt-1">
                Waarnemingen {relativeTime(meta?.datasets?.[0]?.last_success_at)} ·
                weer {relativeTime(meta?.weather?.as_of)} · model{" "}
                {meta?.model_version} {me?.email ? `· ${me.email}` : ""}
              </div>
            </footer>
          </div>
        )}
      </main>
    </div>
  )
}

function Welcome({ onExample }: { onExample: (q: string) => void }) {
  return (
    <div className="hero mx-auto flex max-w-3xl flex-col items-center px-6 py-20 text-center">
      <MushroomIcon className="size-10 text-primary" />
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">
        Wat staat er waar je heen gaat?
      </h1>
      <p className="mt-3 max-w-xl text-sm text-muted-foreground">
        Zoek een plek in Nederland. Je krijgt de soorten die daar bekend zijn,
        op volgorde van hoe dicht ze bij hun piek zitten. Daaronder wat er de
        laatste tijd in de buurt gemeld is.
      </p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((e) => (
          <button
            key={e}
            className="rounded-full border px-3 py-1 text-xs hover:bg-muted"
            onClick={() => onExample(e)}
            title="Vul het zoekveld"
          >
            {e}
          </button>
        ))}
      </div>
    </div>
  )
}

function PlaceHeader({
  place,
  radiusKm,
  onRadius,
  viewDate,
  onDate,
  weather,
  result,
}: {
  place: Place
  radiusKm: number
  onRadius: (n: number) => void
  viewDate: string
  onDate: (d: string) => void
  weather?: Weather
  result: ExpectResult | null
}) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold">{place.name}</h2>
            <p className="text-xs text-muted-foreground">
              {[typeNl(place.type), place.municipality, place.province].filter(Boolean).join(" · ")}
              {" · "}
              {place.lat.toFixed(4)}, {place.lon.toFixed(4)}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <label className="flex items-center gap-1.5">
              straal
              <select
                value={radiusKm}
                onChange={(e) => onRadius(Number(e.target.value))}
                className="h-7 rounded border border-border bg-background px-1.5"
              >
                <option value={2}>2 km</option>
                <option value={5}>5 km</option>
                <option value={10}>10 km</option>
                <option value={20}>20 km</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">
              datum
              <input
                type="date"
                value={viewDate}
                min="2005-01-01"
                onChange={(e) => onDate(e.target.value)}
                className="h-7 rounded border border-border bg-background px-1.5"
              />
            </label>
            <div className="flex items-center gap-0.5">
              <Button
                size="sm"
                variant={viewDate === todayIso() ? "secondary" : "ghost"}
                onClick={() => onDate(todayIso())}
              >
                Nu
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onDate(addDays(todayIso(), 7))}>
                +1 wk
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onDate(addDays(todayIso(), 14))}>
                +2 wk
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onDate(addDays(todayIso(), 28))}>
                +4 wk
              </Button>
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          {result && <Badge>week {result.week}</Badge>}
          {result && (
            <Badge className={result.is_future ? "bg-accent/50 text-accent-foreground" : ""}>
              {result.is_future ? "verwachting voor die datum" : "datum is vandaag of eerder"}
            </Badge>
          )}
          <Badge>{result?.known_total ?? 0} soorten bekend hier</Badge>
          {weather?.as_of && (
            <>
              <Badge>regen 14 d {weather.precip_14d ?? 0} mm</Badge>
              <Badge>{weather.dry_days ?? 0} droge dagen</Badge>
              {weather.temp_c !== undefined && <Badge>{weather.temp_c} °C</Badge>}
              {weather.soil_moisture !== undefined && (
                <Badge>bodem {Math.round((weather.soil_moisture ?? 0) * 100)}%</Badge>
              )}
            </>
          )}
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          De seizoensscore komt uit landelijke waarnemingen van de gekozen week,
          dus je kunt ook vooruitkijken. Het weer hierboven is van vandaag en
          telt niet mee in de volgorde.
        </p>
      </CardContent>
    </Card>
  )
}

function LikelyList({ data }: { data: ExpectResult }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-3 flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">Waarschijnlijk deze periode</h3>
          <span className="text-[11px] text-muted-foreground">
            op seizoen en eerdere meldingen
          </span>
        </div>
        <ol className="space-y-1.5">
          {data.species.map((s, i) => (
            <li
              key={s.species_id}
              className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-muted"
            >
              {s.image_url ? (
                <a
                  href={s.image_credit_url ?? s.image_url}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0"
                  title={`Photo: ${s.image_credit ?? "Wikimedia Commons"}`}
                >
                  <img
                    src={s.image_url}
                    alt={s.name_nl ?? s.scientific_name}
                    loading="lazy"
                    className="size-14 rounded-lg object-cover ring-1 ring-foreground/10"
                  />
                </a>
              ) : (
                <span className="grid size-14 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                  <MushroomIcon className="size-5" />
                </span>
              )}
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {i + 1}.
                  </span>
                  <span className="font-medium">
                    {s.name_nl ?? s.scientific_name}
                  </span>
                  <Badge className={phaseTone(s.seasonal)}>{s.phase}</Badge>
                  <Badge>{Math.round(s.seasonal * 100)}% van piek</Badge>
                  {s.period_records > 0 && (
                    <Badge className="bg-accent/50 text-accent-foreground">
                      {s.period_records}× gemeld rond die tijd
                    </Badge>
                  )}
                </span>
                <span className="mt-0.5 block text-[11px] italic text-muted-foreground">
                  {s.scientific_name}
                </span>
                <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-muted">
                  <span
                    className="block h-full rounded-full bg-primary"
                    style={{ width: `${Math.round(s.seasonal * 100)}%` }}
                  />
                </span>
                <span className="mt-1 block text-[11px] text-muted-foreground">
                  {s.reasons.join(" · ")}
                </span>
              </span>
              <span className="w-10 shrink-0 pt-0.5 text-right text-xs tabular-nums text-muted-foreground">
                {Math.round(s.expected * 100)}
              </span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

function RecentCard({
  reports,
  radiusKm,
}: {
  reports: RecentReport[]
  radiusKm: number
}) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="text-sm font-semibold">Recent gemeld in de buurt</h3>
          <span className="text-[11px] text-muted-foreground">
            {reports.length} in 90 dagen
          </span>
        </div>
        {reports.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Geen meldingen binnen {radiusKm} km in de laatste 90 dagen. Probeer
            een grotere straal.
          </p>
        ) : (
          <ul className="space-y-1">
            {reports.slice(0, 14).map((r) => (
              <li key={r.id} className="flex items-start gap-2 text-xs">
                <span
                  className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-md text-[10px] font-semibold ${
                    r.days_ago <= 30
                      ? "bg-accent/60 text-accent-foreground"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {r.days_ago}d
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">
                    {r.name_nl ?? r.scientific_name}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {r.observed_on} · {r.precise ? "1 km nauwkeurig" : "5 km vak"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {reports.length > 0 && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Iemand zag hier recent wat. De moeite waard, maar geen bewijs dat
            het er nog staat.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export default App
