import type { CSSProperties } from 'react'

const paths = {
  camera: 'M14 4l2 3h4a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V9a2 2 0 012-2h4l2-3z M16 13a4 4 0 11-8 0 4 4 0 018 0',
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  upload: 'M12 16V3 M7 8l5-5 5 5 M3 15v5a1 1 0 001 1h16a1 1 0 001-1v-5',
  search: 'M21 21l-5-5 M18 10a8 8 0 11-16 0 8 8 0 0116 0',
  refresh: 'M20 7a9 9 0 00-15-3L2 7 M2 2v5h5 M4 17a9 9 0 0015 3l3-3 M22 22v-5h-5',
  chevron: 'M9 5l7 7-7 7',
  close: 'M6 6l12 12 M6 18L18 6',
  info: 'M12 16v-4 M12 8h.01 M22 12a10 10 0 11-20 0 10 10 0 0120 0',
  alert: 'M12 8v5 M12 17h.01 M10 3L1 19a1 1 0 001 2h20a1 1 0 001-2L14 3a2 2 0 00-4 0',
  check: 'M5 12l4 4L19 6',
  clock: 'M12 7v5l3 2 M22 12a10 10 0 11-20 0 10 10 0 0120 0',
  image: 'M3 3h18v18H3z M3 16l5-5 4 4 4-6 5 7 M8 7h.01',
  shield: 'M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6z M8 12l3 3 5-6',
  file: 'M14 2H4v20h16V8z M14 2v6h6 M8 13h8 M8 17h5',
} satisfies Record<string, string>

export function Icon({ name, size = 20, style }: {
  name: keyof typeof paths; size?: number; style?: CSSProperties
}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>
    <path d={paths[name]} />
  </svg>
}
