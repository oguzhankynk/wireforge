/** The WireForge mark: brackets around a digital signal (the same drawing as public/favicon.svg). */
export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 800 800" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="wireforge-logo-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6b8cff" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect width="800" height="800" rx="184" fill="url(#wireforge-logo-bg)" />
      <g fill="none" stroke="#ffffff" strokeLinecap="round" strokeLinejoin="round">
        <path d="M250 270 H200 V530 H250" strokeWidth="40" />
        <path d="M550 270 H600 V530 H550" strokeWidth="40" />
        <path d="M270 460 H320 V340 H390 V460 H440 V340 H530" strokeWidth="36" />
      </g>
    </svg>
  );
}
