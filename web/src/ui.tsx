import type { CSSProperties } from "react";

function hue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return h;
}

export function Avatar({
  name,
  size = 36,
  human = false,
  className = "",
}: {
  name: string;
  size?: number;
  human?: boolean;
  className?: string;
}) {
  const h = hue(name || "?");
  const style: CSSProperties = {
    width: size,
    height: size,
    fontSize: Math.round(size * 0.42),
    background: human ? "var(--avatar-bg)" : `hsl(${h} 14% 40%)`,
  };
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  return (
    <span className={`avatar ${className}`} style={style} aria-hidden="true">
      {initial}
    </span>
  );
}

export function Logo({ size = 34 }: { size?: number }) {
  return (
    <span className="logo" style={{ width: size, height: size, fontSize: size * 0.5 }}>
      M
    </span>
  );
}

export function PresenceDot({
  online,
  size = 8,
  className = "",
}: {
  online: boolean;
  size?: number;
  className?: string;
}) {
  const label = online ? "在线" : "离线";
  return (
    <span
      className={`presence-dot ${online ? "online" : "offline"} ${className}`.trim()}
      style={{ width: size, height: size }}
      role="img"
      aria-label={label}
      title={label}
    />
  );
}

type IconProps = { size?: number };
const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

export const IconChat = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
  </svg>
);

export const IconGuide = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
    <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
  </svg>
);

export const IconFlow = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <circle cx="6" cy="6" r="3" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="12" r="3" />
    <path d="M6 9v6" />
    <path d="M9 6h4a2 2 0 0 1 2 2v2" />
    <path d="M9 18h4a2 2 0 0 0 2-2v-2" />
  </svg>
);

export const IconSend = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M22 2 11 13" />
    <path d="M22 2 15 22l-4-9-9-4z" />
  </svg>
);

export const IconBolt = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M13 2 3 14h8l-1 8 10-12h-8z" />
  </svg>
);

export const IconLogout = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <path d="M16 17l5-5-5-5" />
    <path d="M21 12H9" />
  </svg>
);

export const IconFriends = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

export const IconAdmin = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </svg>
);

export const IconPlug = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M9 2v6" />
    <path d="M15 2v6" />
    <path d="M6 8h12v3a6 6 0 0 1-12 0z" />
    <path d="M12 17v5" />
  </svg>
);

export const IconPlus = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </svg>
);

export const IconSettings = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
);

export const IconUserPlus = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M20 8v6" />
    <path d="M23 11h-6" />
  </svg>
);

export const IconTrash = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M3 6h18" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
);

export const IconKey = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
  </svg>
);

export const IconCheck = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

export const IconX = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M18 6L6 18" />
    <path d="M6 6l12 12" />
  </svg>
);

export const IconChevronRight = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M9 18l6-6-6-6" />
  </svg>
);

export const IconChevronLeft = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M15 18l-6-6 6-6" />
  </svg>
);

export const IconTemplate = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M4 3h12l4 4v14H4z" />
    <path d="M16 3v4h4" />
    <path d="M8 12h8" />
    <path d="M8 16h5" />
  </svg>
);

export const IconExpand = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <path d="M15 3h6v6" />
    <path d="M9 21H3v-6" />
    <path d="M21 3l-7 7" />
    <path d="M3 21l7-7" />
  </svg>
);

export const IconBan = ({ size = 18 }: IconProps) => (
  <svg {...base(size)}>
    <circle cx="12" cy="12" r="10" />
    <path d="M4.93 4.93l14.14 14.14" />
  </svg>
);