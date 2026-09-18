/**
 * Lucide-style paddenstoeltje. Lucide zelf heeft er geen, en een los icoon
 * is sneller en kleiner dan er een pakket voor ophalen.
 */
export function MushroomIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {/* hoed */}
      <path d="M4 12.2C4 7.7 7.6 4.2 12 4.2s8 3.5 8 8c0 .6-.5 1.1-1.1 1.1H5.1C4.5 13.3 4 12.8 4 12.2Z" />
      {/* steel */}
      <path d="M9.4 13.3v5.4a2.6 2.6 0 0 0 5.2 0v-5.4" />
      {/* stippen op de hoed */}
      <path d="M8.6 9.4h.01" />
      <path d="M14.9 8.6h.01" />
      <path d="M11.9 7.2h.01" />
    </svg>
  )
}
