// Значки — простые линейные SVG (чёткие при любом масштабе, без шрифтов значков Windows).
const PATHS: Record<string, string> = {
  menu: 'M4 6h16M4 12h16M4 18h16',
  search: 'M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14zM21 21l-5-5',
  ask: 'M4 5h16v10H9l-5 4z M9 10h.01 M12 10h.01 M15 10h.01',
  home: 'M3 11l9-7 9 7 M5 10v10h14V10 M10 20v-6h4v6',
  inbox: 'M3 6h18v12H3z M3 7l9 6 9-6',
  ready: 'M5 12l5 5 9-10',
  flag: 'M6 21V4 M6 4h11l-2 4 2 4H6',
  send: 'M3 11l18-8-8 18-2-8z',
  chart: 'M4 20V4 M4 20h16 M8 16v-5 M12 16V8 M16 16v-8',
  alert: 'M12 3l10 18H2z M12 10v5 M12 18h.01',
  doc: 'M6 3h9l4 4v14H6z M14 3v5h5 M9 13h7 M9 17h7',
  people: 'M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7z M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5 M16 11a3 3 0 1 0 0-6 M18 14.5c1.9.6 3.1 2.4 3.5 5.5',
  history: 'M3 12a9 9 0 1 0 3-6.7 M3 4v5h5 M12 7v5l3 3',
  admin: 'M12 9a3 3 0 1 0 0 6a3 3 0 0 0 0-6z M12 2v3 M12 19v3 M2 12h3 M19 12h3 M4.9 4.9l2.1 2.1 M17 17l2.1 2.1 M4.9 19.1L7 17 M17 7l2.1-2.1',
  key: 'M14 3a5 5 0 1 0 0 10a5 5 0 0 0 0-10z M10.5 11.5L3 19v2h3v-2h2v-2h2l1.5-1.5',
  power: 'M12 3v9 M6.3 6.3a8 8 0 1 0 11.4 0',
  mail: 'M3 6h18v12H3z M3 7l9 6 9-6',
  plus: 'M12 5v14 M5 12h14',
  edit: 'M4 20h4L19 9l-4-4L4 16z M14 6l4 4',
  trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13',
  lock: 'M6 11h12v9H6z M8 11V7a4 4 0 0 1 8 0v4',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7',
  open: 'M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6',
  play: 'M7 4l13 8-13 8z',
  download: 'M12 4v11 M7 10l5 5 5-5 M4 20h16',
  read: 'M3 9l9-6 9 6v11H3z M3 9l9 6 9-6',
  help: 'M12 21a9 9 0 1 0 0-18a9 9 0 0 0 0 18z M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7 M12 17h.01',
  close: 'M6 6l12 12 M18 6L6 18',
}

export function Icon({ name, size = 20, className }: { name: string; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] || ''} />
    </svg>
  )
}
