import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  CloudRain,
  Database,
  Leaf,
  Loader2,
  Moon,
  RefreshCw,
  Search,
  Sun,
} from "lucide-react"

import { Badge } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { MapView, type MapControls } from "@/components/MapView"
import {
  analyseArea,
  cancelRun,
  fetchCandidates,
  fetchFineCells,
  fetchHotspots,
  fetchMe,
  fetchMeta,
  fetchSpecies,
  startRefresh,
  type Candidate,
  type FineCell,
  type Hotspot,
  type Me,
  type Meta,
  type Species,
} from "@/lib/api"

type Mode = "history" | "recent" | "fine"
type SelectedKind = "history" | "recent" | "aoi"

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

function relativeTime(iso: string | null): string {
  if (!iso) return "never"
  const then = new Date(iso).getTime()
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

function scoreBadge(score: number): string {
  if (score >= 0.5) return "bg-primary/15 text-primary"
  if (score >= 0.3) return "bg-accent/40 text-accent-foreground"
  return "bg-muted text-muted-foreground"
}

export function App() {
  const { theme, setTheme } = useTheme()
  const [me, setMe] = useState<Me | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [hotspots, setHotspots] = useState<Hotspot[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [fineCells, setFineCells] = useState<FineCell[]>([])
  const [guilds, setGuilds] = useState<Record<string, string>>({})
  const [guild, setGuild] = useState<string>("")
  const [mode, setMode] = useState<Mode>("history")
  const [recentDays, setRecentDays] = useState(90)
  const [showHotspots, setShowHotspots] = useState(true)
  const [showCandidates, setShowCandidates] = useState(true)
  const [showFine, setShowFine] = useState(true)
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<{ kind: SelectedKind; id: string } | null>(null)
  const [species, setSpecies] = useState<Species[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Rendering thousands of rows both overwhelms the layout and hurts the map,
  // so the DOM list is capped and grows on demand.
  const [listLimit, setListLimit] = useState(60)
  const controls = useRef<MapControls | null>(null)

  useEffect(() => {
    setListLimit(60)
  }, [guild, query, mode])

  const loadRuns = useCallback(async () => {
    try {
      const metaRes = await fetchMeta()
      setMeta(metaRes)
      return metaRes.runs
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return []
    }
  }, [])

  const loadData = useCallback(async () => {
    try {
      const [h, c, f] = await Promise.all([
        fetchHotspots(guild || undefined),
        fetchCandidates(guild || undefined),
        // days=0 -> all precise cells; the UI applies the recency filter so
        // switching the window does not need a refetch.
        fetchFineCells(guild || undefined, 0),
      ])
      setHotspots(h.hotspots)
      setGuilds(h.guilds)
      setCandidates(c.candidates)
      setFineCells(f.cells)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [guild])

  useEffect(() => {
    ;(async () => {
      try {
        setMe(await fetchMe())
      } catch {
        /* identity is best-effort for read-only browsing */
      }
      await loadRuns()
      await loadData()
    })()
  }, [loadRuns, loadData])

  // Poll while a refresh is active so progress is live.
  useEffect(() => {
    const active = meta?.runs.some(
      (r) => r.status === "running" || r.status === "queued"
    )
    if (!active) return
    const t = setInterval(async () => {
      const runs = await loadRuns()
      if (!runs.some((r) => r.status === "running" || r.status === "queued")) {
        await loadData()
      }
    }, 4000)
    return () => clearInterval(t)
  }, [meta, loadRuns, loadData])

  useEffect(() => {
    if (!selected || selected.kind !== "history") {
      setSpecies([])
      return
    }
    fetchSpecies(selected.id)
      .then((r) => setSpecies(r.species))
      .catch(() => setSpecies([]))
  }, [selected])

  const selectedHotspot = useMemo(
    () =>
      selected?.kind === "history"
        ? hotspots.find((h) => h.cell_id === selected.id) ?? null
        : null,
    [selected, hotspots]
  )
  const selectedCandidate = useMemo(
    () =>
      selected?.kind === "aoi"
        ? candidates.find((c) => c.id === selected.id) ?? null
        : null,
    [selected, candidates]
  )
  const selectedFine = useMemo(
    () =>
      selected?.kind === "recent"
        ? fineCells.find((c) => c.cell_id === selected.id) ?? null
        : null,
    [selected, fineCells]
  )

  const onSelect = useCallback(
    (kind: "hotspot" | "candidate" | "fine", id: string) => {
      const map: Record<string, SelectedKind> = {
        hotspot: "history",
        fine: "recent",
        candidate: "aoi",
      }
      setSelected({ kind: map[kind] ?? "history", id })
    },
    []
  )

  const trigger = async (kind: "weather" | "historical") => {
    setBusy(kind)
    setNotice(null)
    try {
      await startRefresh(kind)
      setNotice(
        kind === "weather"
          ? "Weather refresh queued."
          : "Historical data refresh queued."
      )
      await loadRuns()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setNotice(msg.includes("already running") ? "That refresh is already running." : msg)
    } finally {
      setBusy(null)
    }
  }

  const analyse = async () => {
    const b = controls.current?.getBounds()
    if (!b) return
    setBusy("aoi")
    setNotice(null)
    try {
      await analyseArea(b, guild || "mycorrhizal")
      setMode("fine")
      setNotice("10 m analysis started for the visible area (max 2 km).")
      await loadRuns()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const visibleHotspots = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return hotspots
    return hotspots.filter(
      (h) =>
        h.guild_label.toLowerCase().includes(q) ||
        h.cell_id.toLowerCase().includes(q)
    )
  }, [hotspots, query])

  // Precise 1 km cells, newest first, optionally limited to a recent window.
  const visibleFine = useMemo(() => {
    return fineCells
      .filter((c) => c.days_ago !== null && c.days_ago <= recentDays)
      .sort((a, b) => (a.days_ago ?? 9999) - (b.days_ago ?? 9999) || b.n - a.n)
  }, [fineCells, recentDays])

  const resultTotal =
    mode === "history"
      ? visibleHotspots.length
      : mode === "recent"
        ? visibleFine.length
        : candidates.length
  const weather = meta?.weather
  const activeRun = meta?.runs.find(
    (r) => r.status === "running" || r.status === "queued"
  )

  return (
    <div className="flex h-svh flex-col bg-background text-foreground">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-card/80 px-3 backdrop-blur">
        <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
          <Leaf className="size-4" />
        </span>
        <div className="mr-2 hidden sm:block">
          <div className="text-sm font-semibold leading-none">Mushroom Finder</div>
          <div className="text-[11px] text-muted-foreground">
            search priority, not occurrence
          </div>
        </div>

        <select
          value={guild}
          onChange={(e) => setGuild(e.target.value)}
          className="h-8 rounded-lg border border-border bg-background px-2 text-sm"
          title="Filter by mushroom group"
        >
          <option value="">All groups</option>
          {Object.entries(guilds).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>

        <div className="hidden items-center rounded-lg border border-border p-0.5 md:flex">
          <Button
            size="sm"
            variant={mode === "history" ? "secondary" : "ghost"}
            onClick={() => setMode("history")}
            title="5 x 5 km evidence zones"
          >
            Historical
          </Button>
          <Button
            size="sm"
            variant={mode === "recent" ? "secondary" : "ghost"}
            onClick={() => setMode("recent")}
            title="1 km cells from precise records"
          >
            Precise (1 km)
          </Button>
          <Button
            size="sm"
            variant={mode === "fine" ? "secondary" : "ghost"}
            onClick={() => setMode("fine")}
          >
            10 m targets
          </Button>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
            <CloudRain className="size-3.5" />
            <span>
              {weather?.as_of
                ? `weather ${relativeTime(weather.as_of)} · condition ${(weather.condition ?? 0).toFixed(2)}`
                : "weather unavailable"}
            </span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => trigger("weather")}
            disabled={busy === "weather" || !!activeRun}
            title="Refresh the current weather / fruiting-condition inputs"
          >
            {busy === "weather" ? (
              <Loader2 className="animate-spin" />
            ) : (
              <CloudRain />
            )}
            <span className="hidden sm:inline">Weather</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => trigger("historical")}
            disabled={busy === "historical" || !!activeRun}
            title="Re-import historical observations and rebuild coarse scores"
          >
            {busy === "historical" ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Database />
            )}
            <span className="hidden sm:inline">Historical</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title="Toggle theme"
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
      </header>

      {activeRun && (
        <div className="flex items-center gap-2 border-b bg-accent/30 px-3 py-1.5 text-xs">
          <Loader2 className="size-3.5 animate-spin" />
          <span className="font-medium">{activeRun.kind}</span>
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
            Cancel
          </Button>
        </div>
      )}

      {notice && (
        <div className="border-b bg-secondary/50 px-3 py-1.5 text-xs">{notice}</div>
      )}
      {error && (
        <div className="border-b border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Sidebar: results. Bounded height so the map stays visible when the
            layout stacks on narrow viewports. */}
        <aside className="flex max-h-[42vh] min-h-0 w-full shrink-0 flex-col border-b lg:max-h-none lg:h-full lg:w-[400px] lg:border-r lg:border-b-0">
          <div className="hero border-b px-3 py-3">
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter results…"
                  className="h-8 w-full rounded-lg border border-border bg-background pl-8 pr-2 text-sm"
                />
              </div>
              <Button variant="outline" size="sm" onClick={analyse} disabled={busy === "aoi"}>
                {busy === "aoi" ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                10 m now
              </Button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={showHotspots}
                  onChange={(e) => setShowHotspots(e.target.checked)}
                />
                historical zones
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={showFine}
                  onChange={(e) => setShowFine(e.target.checked)}
                />
                precise (1 km)
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={showCandidates}
                  onChange={(e) => setShowCandidates(e.target.checked)}
                />
                10 m targets
              </label>
              <label className="flex items-center gap-1.5">
                <span>since</span>
                <select
                  value={recentDays}
                  onChange={(e) => setRecentDays(Number(e.target.value))}
                  className="h-6 rounded border border-border bg-background px-1 text-[11px]"
                  title="Only show precise cells with a report in this window"
                >
                  <option value={14}>14 days</option>
                  <option value={30}>30 days</option>
                  <option value={90}>90 days</option>
                  <option value={3650}>all time</option>
                </select>
              </label>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {mode === "history" ? (
              visibleHotspots.length === 0 ? (
                <Empty text="No hotspots yet. Run a historical refresh to build the evidence layer." />
              ) : (
                visibleHotspots.slice(0, listLimit).map((h) => (
                  <button
                    key={h.cell_id}
                    onClick={() => setSelected({ kind: "history", id: h.cell_id })}
                    className={`mb-1.5 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-muted ${
                      selected?.id === h.cell_id ? "bg-muted" : ""
                    }`}
                  >
                    <span
                      className={`grid size-9 shrink-0 place-items-center rounded-lg text-xs font-semibold ${scoreBadge(h.final_score)}`}
                    >
                      {h.final_score.toFixed(2)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {h.guild_label}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {h.richness} taxa · {h.confidence} confidence ·{" "}
                        {h.last_seen ? `last ${h.last_seen}` : "no recent record"}
                      </span>
                    </span>
                  </button>
                ))
              )
            ) : mode === "recent" ? (
              visibleFine.length === 0 ? (
                <Empty
                  text={`No precise reports in the last ${recentDays} days. Precise records come from datasets with real coordinates (mainly iNaturalist); most Dutch records are rounded to 5 km.`}
                />
              ) : (
                visibleFine.slice(0, listLimit).map((f) => (
                  <button
                    key={f.cell_id + f.guild}
                    onClick={() => setSelected({ kind: "recent", id: f.cell_id })}
                    className={`mb-1.5 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-muted ${
                      selected?.id === f.cell_id ? "bg-muted" : ""
                    }`}
                  >
                    <span
                      className={`grid size-9 shrink-0 place-items-center rounded-lg text-[10px] font-semibold ${
                        f.days_ago !== null && f.days_ago <= 30
                          ? "bg-accent/60 text-accent-foreground"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {f.days_ago === null ? "?" : `${f.days_ago}d`}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {f.guild_label}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {f.n} precise records · {f.years} years · last{" "}
                        {f.last_seen ?? "?"}
                      </span>
                    </span>
                  </button>
                ))
              )
            ) : candidates.length === 0 ? (
              <Empty text="No 10 m targets yet. Zoom into a forest or park and press '10 m now' (max 2 km across)." />
            ) : (
              candidates.slice(0, listLimit).map((c) => (
                <button
                  key={c.id}
                  onClick={() => setSelected({ kind: "aoi", id: c.id })}
                  className={`mb-1.5 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-muted ${
                    selected?.id === c.id ? "bg-muted" : ""
                  }`}
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/15 text-xs font-semibold text-primary">
                    {c.fsp.toFixed(2)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {c.guild_label}
                    </span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      10 m search target · {c.bucket}
                    </span>
                  </span>
                </button>
              ))
            )}
            {resultTotal > listLimit && (
              <div className="p-2 text-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setListLimit((n) => n + 120)}
                >
                  Show more ({resultTotal - listLimit} more of {resultTotal})
                </Button>
              </div>
            )}
          </div>
        </aside>

        {/* Map + detail */}
        <main className="relative min-h-[52vh] flex-1 lg:min-h-0">
          <MapView
            hotspots={showHotspots ? visibleHotspots : []}
            candidates={showCandidates ? candidates : []}
            fineCells={showFine ? visibleFine : []}
            showHotspots={showHotspots}
            showCandidates={showCandidates}
            showFine={showFine}
            selectedId={selected?.id ?? null}
            onSelect={onSelect}
            controls={controls}
          />

          <div className="pointer-events-none absolute top-2 left-2 z-[500] max-w-[240px]">
            <div className="pointer-events-auto rounded-lg bg-card/90 px-2.5 py-2 text-[11px] ring-1 ring-foreground/10 backdrop-blur">
              <div className="flex items-center gap-1.5 font-medium">
                {mode === "history"
                  ? "Historical evidence"
                  : mode === "recent"
                    ? "Precise reports (1 km)"
                    : "10 m search targets"}
              </div>
              <p className="mt-0.5 text-muted-foreground">
                {mode === "history"
                  ? "5 x 5 km zones where a group is repeatedly recorded. Broad, not exact."
                  : mode === "recent"
                    ? "1 km cells from records that carry real coordinates (mainly recent iNaturalist). Amber = reported in the last 30 days."
                    : "Experimental habitat clues inside a selected area. Search targets, never mushroom locations."}
              </p>
            </div>
          </div>

          {(selectedHotspot || selectedCandidate || selectedFine) && (
            <Card className="absolute right-2 bottom-2 z-[500] max-h-[60%] w-[min(380px,calc(100%-1rem))] overflow-y-auto bg-card/95 backdrop-blur">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle>
                    {selectedHotspot
                      ? selectedHotspot.guild_label
                      : selectedCandidate
                        ? selectedCandidate.guild_label
                        : selectedFine?.guild_label}
                  </CardTitle>
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setSelected(null)}
                    title="Close"
                  >
                    ×
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 text-xs">
                {selectedHotspot && (
                  <>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge className={scoreBadge(selectedHotspot.final_score)}>
                        priority {selectedHotspot.final_score.toFixed(2)}
                      </Badge>
                      <Badge>static {selectedHotspot.static_score.toFixed(2)}</Badge>
                      <Badge>{selectedHotspot.confidence} confidence</Badge>
                      <Badge>season {selectedHotspot.season_factor.toFixed(2)}</Badge>
                    </div>
                    <ComponentBars components={selectedHotspot.components} />
                    <div>
                      <div className="mb-1 font-medium">Likely taxa here</div>
                      {species.length === 0 ? (
                        <p className="text-muted-foreground">No curated taxa recorded.</p>
                      ) : (
                        <ul className="space-y-0.5">
                          {species.map((s) => (
                            <li key={s.scientific_name} className="flex justify-between gap-2">
                              <span className="truncate">
                                {s.name_nl ?? s.scientific_name}
                              </span>
                              <span className="shrink-0 text-muted-foreground">
                                {s.n}× · {s.last_seen ?? ""}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    <p className="text-muted-foreground">
                      Historical records are shown only at 5 x 5 km. Exact finds are
                      never published.
                    </p>
                  </>
                )}
                {selectedFine && (
                  <>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge className="bg-accent/60 text-accent-foreground">
                        precise records
                      </Badge>
                      <Badge>{selectedFine.n} records</Badge>
                      <Badge>{selectedFine.years} years</Badge>
                      {selectedFine.days_ago !== null && (
                        <Badge>
                          {selectedFine.days_ago === 0
                            ? "today"
                            : `${selectedFine.days_ago} days ago`}
                        </Badge>
                      )}
                    </div>
                    <div className="space-y-0.5 text-muted-foreground">
                      <div>First seen {selectedFine.first_seen ?? "?"}</div>
                      <div>Last seen {selectedFine.last_seen ?? "?"}</div>
                      <div>{selectedFine.recent_n} records in the last 30 days</div>
                    </div>
                    <p className="text-muted-foreground">
                      Built only from records with usable coordinates, shown at
                      1 km. This is where someone recently reported one of the
                      curated species, so it is worth a look — not proof it is
                      still fruiting. Exact spots are never shown.
                    </p>
                  </>
                )}
                {selectedCandidate && (
                  <>
                    <div className="flex flex-wrap gap-1.5">
                      <Badge className="bg-primary/15 text-primary">
                        {selectedCandidate.bucket}
                      </Badge>
                      <Badge>experimental habitat clue</Badge>
                      <Badge>grade {selectedCandidate.confidence}</Badge>
                    </div>
                    <ComponentBars components={selectedCandidate.components} />
                    <p className="text-muted-foreground">
                      A 10 m square built from terrain, canopy and mapped habitat.
                      It ranks where to look, not where mushrooms are. Verify local
                      access rules and stay on permitted paths.
                    </p>
                  </>
                )}
              </CardContent>
            </Card>
          )}

          <div className="pointer-events-none absolute bottom-1 left-1 z-[400] max-w-[70%] text-[10px] text-muted-foreground">
            {meta?.attributions.map((a) => (
              <span key={a.id} className="mr-2">
                <a className="pointer-events-auto underline" href={a.url} target="_blank" rel="noreferrer">
                  {a.label}
                </a>
              </span>
            ))}
          </div>
        </main>
      </div>

      <footer className="flex h-8 shrink-0 items-center gap-3 border-t px-3 text-[11px] text-muted-foreground">
        <span>
          <RefreshCw className="mr-1 inline size-3" />
          historical {relativeTime(meta?.datasets?.[0]?.last_success_at ?? null)}
        </span>
        <span>
          <CloudRain className="mr-1 inline size-3" />
          weather {relativeTime(meta?.weather?.as_of ?? null)}
        </span>
        <span className="ml-auto hidden sm:inline">
          model {meta?.model_version ?? "…"} · private household use ·{" "}
          {me?.email || "signed in"}
        </span>
      </footer>
    </div>
  )
}

function ComponentBars({ components }: { components: Record<string, number | string> }) {
  const numeric = Object.entries(components).filter(
    ([k, v]) => typeof v === "number" && !k.includes("version")
  ) as Array<[string, number]>
  if (numeric.length === 0) return null
  return (
    <div className="space-y-1">
      {numeric.map(([key, value]) => (
        <div key={key} className="flex items-center gap-2">
          <span className="w-24 shrink-0 capitalize text-muted-foreground">
            {key.replace(/_/g, " ")}
          </span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${Math.round(Math.min(1, value) * 100)}%` }}
            />
          </span>
          <span className="w-8 shrink-0 text-right tabular-nums">
            {value.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="grid place-items-center gap-2 px-4 py-12 text-center">
      <Leaf className="size-6 text-muted-foreground" />
      <p className="text-xs text-muted-foreground">{text}</p>
    </div>
  )
}
