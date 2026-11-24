import React from "react";

const Svg = ({ children, size = 20, className = "", viewBox = "0 0 24 24", ...rest }) => (
  <svg
    width={size}
    height={size}
    viewBox={viewBox}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
    {...rest}
  >
    {children}
  </svg>
);

export const Logo = (p) => (
  <Svg {...p}>
    <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" />
    <path d="m4 7 8 4 8-4M12 11v10" />
    <circle cx="12" cy="11" r="2.2" />
  </Svg>
);

export const ChatIcon = (p) => (
  <Svg {...p}>
    <path d="M21 11.5a8.5 8.5 0 0 1-12.6 7.4L3 21l1.8-4.6A8.5 8.5 0 1 1 21 11.5Z" />
  </Svg>
);

export const SettingsIcon = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.8 1.8 0 0 0 .4 2l.1.1-2.1 2.1-.1-.1a1.8 1.8 0 0 0-2-.4 1.8 1.8 0 0 0-1 1.6V21h-3v-.3A1.8 1.8 0 0 0 10.7 19a1.8 1.8 0 0 0-2 .4l-.1.1L6.5 17.4l.1-.1a1.8 1.8 0 0 0 .4-2 1.8 1.8 0 0 0-1.6-1H5v-3h.3A1.8 1.8 0 0 0 7 10.3a1.8 1.8 0 0 0-.4-2L6.5 8.2 8.6 6.1l.1.1a1.8 1.8 0 0 0 2 .4H11V6h3v.5a1.8 1.8 0 0 0 1 1.6 1.8 1.8 0 0 0 2-.4l.1-.1 2.1 2.1-.1.1a1.8 1.8 0 0 0-.4 2 1.8 1.8 0 0 0 1.6 1H21v3h-.5a1.8 1.8 0 0 0-1.1 1Z" />
  </Svg>
);

export const SendIcon = (p) => (
  <Svg {...p}>
    <path d="m4 12 16-8-6 16-2.5-6L4 12Z" />
    <path d="m11.5 14 4-4" />
  </Svg>
);

export const TrashIcon = (p) => (
  <Svg {...p}>
    <path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14" />
  </Svg>
);

export const BotIcon = (p) => (
  <Svg {...p}>
    <rect x="4" y="8" width="16" height="12" rx="3" />
    <path d="M12 4v4M9 14h.01M15 14h.01M9 17h6" />
  </Svg>
);

export const UserIcon = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21a8 8 0 0 1 16 0" />
  </Svg>
);

export const ToolIcon = (p) => (
  <Svg {...p}>
    <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4L15 12l-3-3 2.7-2.7Z" />
  </Svg>
);

export const CheckIcon = (p) => (
  <Svg {...p}>
    <path d="m5 12 4 4L19 6" />
  </Svg>
);

export const CopyIcon = (p) => (
  <Svg {...p}>
    <rect x="9" y="9" width="12" height="12" rx="2" />
    <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
  </Svg>
);

export const RefreshIcon = (p) => (
  <Svg {...p}>
    <path d="M20 11a8 8 0 1 0-2.3 5.7L20 14" />
    <path d="M20 5v6h-6" />
  </Svg>
);

export const CloseIcon = (p) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
);

export const BoltIcon = (p) => (
  <Svg {...p}>
    <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />
  </Svg>
);

export const ShieldIcon = (p) => (
  <Svg {...p}>
    <path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Svg>
);
