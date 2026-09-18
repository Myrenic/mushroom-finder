import { useEffect, useRef } from "react"
import L from "leaflet"

import type { Candidate, FineCell, Hotspot } from "@/lib/api"

export interface MapControls {
  getBounds: () => { south: number; west: number; north: number; east: number } | null
}

interface Props {
  hotspots: Hotspot[]
  candidates: Candidate[]
  fineCells: FineCell[]
  showHotspots: boolean
  showCandidates: boolean
  showFine: boolean
  selectedId: string | null
  onSelect: (kind: "hotspot" | "candidate" | "fine", id: string) => void
  controls: React.MutableRefObject<MapControls | null>
}

// Green (low) -> amber (high), readable in both themes.
function scoreColor(score: number): string {
  const s = Math.max(0, Math.min(1, score))
  const hue = 140 - s * 95
  const light = 62 - s * 14
  return `hsl(${hue} 65% ${light}%)`
}

export function MapView({
  hotspots,
  candidates,
  fineCells,
  showHotspots,
  showCandidates,
  showFine,
  selectedId,
  onSelect,
  controls,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  const selectRef = useRef(onSelect)
  selectRef.current = onSelect

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current, {
      center: [52.15, 5.35],
      zoom: 8,
      preferCanvas: true,
      zoomControl: true,
    })
    L.tileLayer(
      "https://service.pdok.nl/kadaster/brt-achtergrondkaart/wmts/v2_0/standaard/EPSG:3857/{z}/{x}/{y}.png",
      {
        maxZoom: 19,
        attribution:
          'Kaart &copy; <a href="https://www.pdok.nl/copyright">Kadaster / PDOK</a> (CC BY 4.0) · ' +
          'data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> (ODbL)',
      }
    ).addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    controls.current = {
      getBounds: () => {
        const b = map.getBounds()
        return {
          south: b.getSouth(),
          west: b.getWest(),
          north: b.getNorth(),
          east: b.getEast(),
        }
      },
    }
    return () => {
      map.remove()
      mapRef.current = null
      layerRef.current = null
      controls.current = null
    }
  }, [controls])

  // Redraw markers when data or toggles change.
  useEffect(() => {
    const group = layerRef.current
    if (!group) return
    group.clearLayers()

    if (showHotspots) {
      for (const h of hotspots) {
        const marker = L.circleMarker([h.lat, h.lon], {
          radius: 5 + h.final_score * 13,
          color: h.cell_id === selectedId ? "#111" : scoreColor(h.final_score),
          weight: h.cell_id === selectedId ? 3 : 1,
          fillColor: scoreColor(h.final_score),
          fillOpacity: 0.55,
        })
        marker.bindTooltip(
          `${h.guild_label}<br/>search priority ${h.final_score.toFixed(2)} · ${h.confidence} confidence`,
          { direction: "top" }
        )
        marker.on("click", () => selectRef.current("hotspot", h.cell_id))
        group.addLayer(marker)
      }
    }

    if (showFine) {
      for (const f of fineCells) {
        const fresh = f.days_ago !== null && f.days_ago <= 30
        const marker = L.circleMarker([f.lat, f.lon], {
          radius: fresh ? 7 : 4 + Math.min(5, f.n / 4),
          color: fresh ? "#b45309" : "#a16207",
          weight: fresh ? 2 : 1,
          fillColor: fresh ? "#f59e0b" : "#fbbf24",
          fillOpacity: fresh ? 0.9 : 0.55,
        })
        marker.bindTooltip(
          `${f.guild_label}<br/>${f.n} precise records · last ${f.last_seen ?? "?"}` +
            (fresh ? ` · ${f.days_ago} days ago` : ""),
          { direction: "top" }
        )
        marker.on("click", () => selectRef.current("fine", f.cell_id))
        group.addLayer(marker)
      }
    }

    if (showCandidates) {
      for (const c of candidates) {
        const marker = L.circleMarker([c.lat, c.lon], {
          radius: 4,
          color: "#1d4ed8",
          weight: 1,
          fillColor: "#3b82f6",
          fillOpacity: 0.9,
        })
        marker.bindTooltip(
          `10 m search target · ${c.guild_label}<br/>${c.bucket}`,
          { direction: "top" }
        )
        marker.on("click", () => selectRef.current("candidate", c.id))
        group.addLayer(marker)
      }
    }
  }, [hotspots, candidates, fineCells, showHotspots, showCandidates, showFine, selectedId])

  return <div ref={containerRef} className="h-full w-full" />
}
