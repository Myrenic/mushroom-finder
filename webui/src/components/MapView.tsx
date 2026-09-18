import { useEffect, useRef } from "react"
import L from "leaflet"

import type { RecentReport } from "@/lib/api"

interface Props {
  center: { lat: number; lon: number } | null
  radiusKm: number
  reports: RecentReport[]
}

/**
 * Context map: where the searched place is, how big the analysis radius is,
 * and what has been reported nearby. It is deliberately not the interface —
 * the record resolution (5 km) does not support walking to a point.
 */
export function MapView({ center, radiusKm, reports }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = L.map(containerRef.current, {
      center: [52.15, 5.35],
      zoom: 8,
      preferCanvas: true,
      scrollWheelZoom: true,
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
    const invalidate = () => map.invalidateSize()
    const t = window.setTimeout(invalidate, 250)
    window.addEventListener("resize", invalidate)
    return () => {
      window.clearTimeout(t)
      window.removeEventListener("resize", invalidate)
      map.remove()
      mapRef.current = null
      layerRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const group = layerRef.current
    if (!map || !group) return
    group.clearLayers()

    if (center) {
      L.circle([center.lat, center.lon], {
        radius: radiusKm * 1000,
        color: "#15803d",
        weight: 1,
        fillColor: "#22c55e",
        fillOpacity: 0.08,
      }).addTo(group)
      L.circleMarker([center.lat, center.lon], {
        radius: 6,
        color: "#14532d",
        weight: 2,
        fillColor: "#16a34a",
        fillOpacity: 1,
      })
        .bindTooltip("Searched place — records are searched in the circle")
        .addTo(group)
    }

    for (const r of reports) {
      const fresh = r.days_ago <= 30
      L.circleMarker([r.lat, r.lon], {
        radius: r.precise ? 5 : 7,
        color: fresh ? "#b45309" : "#78716c",
        weight: fresh ? 2 : 1,
        fillColor: fresh ? "#f59e0b" : "#a8a29e",
        fillOpacity: fresh ? 0.9 : 0.5,
        dashArray: r.precise ? undefined : "2,2",
      })
        .bindTooltip(
          `${r.name_nl ?? r.scientific_name}<br/>${r.observed_on} · ${r.days_ago} days ago` +
            `<br/>${r.precise ? "1 km precise" : "5 km area (generalised)"}`
        )
        .addTo(group)
    }

    if (center) {
      map.setView([center.lat, center.lon], radiusKm <= 2 ? 13 : radiusKm <= 5 ? 12 : 11)
      window.setTimeout(() => map.invalidateSize(), 100)
    }
  }, [center, radiusKm, reports])

  return <div ref={containerRef} className="h-full w-full rounded-xl" />
}
