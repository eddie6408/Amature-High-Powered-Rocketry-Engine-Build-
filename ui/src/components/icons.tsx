/** Small stroke icons for navigation (inherit currentColor). */
const PATHS: Record<string, string> = {
  home: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  design: "M4 20 20 4M7 20H4v-3M14 4h6v6M9 9l2 2M12 6l2 2M6 12l2 2",
  motors: "M12 3c3 4 5 6 5 10a5 5 0 0 1-10 0c0-2 1-4 2-5 0 2 1 3 2 3 0-3 0-5 1-8z",
  missions: "M3 17l6-6 4 4 8-8M15 7h6v6",
  launch: "M12 2c3 3 4 7 4 11l-2 3h-4l-2-3c0-4 1-8 4-11zM10 16l-3 4M14 16l3 4M12 9.5a1.5 1.5 0 1 0 0-.01",
  checklists: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  readiness: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5",
  ground: "M12 12v9M8 21h8M8.5 8.5a5 5 0 0 1 7 0M5.5 5.5a9 9 0 0 1 13 0M12 12a1 1 0 1 0 0-.01",
  flights: "M3 12h4l3-8 4 16 3-8h4",
  tools: "M14 6a4 4 0 0 1-5 5l-5 5 3 3 5-5a4 4 0 0 0 5-5l-2 2-3-3z",
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.home} />
    </svg>
  );
}

export function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.1}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 2.5c2.6 2.6 3.6 6 3.6 9.6l-1.7 2.6h-3.8l-1.7-2.6c0-3.6 1-7 3.6-9.6zM10.2 14.7 7.5 18.5M13.8 14.7l2.7 3.8M12 17.5v4" />
    </svg>
  );
}
