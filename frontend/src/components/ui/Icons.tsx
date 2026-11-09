import type { ReactNode, SVGProps } from "react";

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  size?: number;
}

function make(paths: ReactNode, strokeWidth = 2) {
  const Icon = ({ size = 20, ...rest }: IconProps) => (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {paths}
    </svg>
  );
  return Icon;
}

/** 線條圖示（路徑取自核准草圖），一律 aria-hidden，由外層按鈕提供名稱。 */
export const Icons = {
  pen: make(
    <>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </>,
  ),
  brush: make(
    <>
      <path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08" />
      <path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z" />
    </>,
  ),
  line: make(<path d="M5 19 19 5" />),
  rect: make(<rect x="4" y="5" width="16" height="14" rx="2" />),
  circle: make(<circle cx="12" cy="12" r="8" />),
  eraser: make(
    <>
      <path d="m7 21-4.3-4.3a1 1 0 0 1 0-1.4l10-10a1 1 0 0 1 1.4 0l5.6 5.6a1 1 0 0 1 0 1.4L13 21" />
      <path d="M22 21H7" />
      <path d="m5 11 9 9" />
    </>,
  ),
  upload: make(
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m17 8-5-5-5 5" />
      <path d="M12 3v12" />
    </>,
  ),
  undo: make(
    <>
      <path d="M3 7v6h6" />
      <path d="M21 17a9 9 0 0 0-15-6.7L3 13" />
    </>,
  ),
  trash: make(
    <>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="m19 6-1 14H6L5 6" />
    </>,
  ),
  wand: make(<path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M3 21l9-9M12.2 6.2 11 5" />),
  download: make(
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10 5 5 5-5" />
      <path d="M12 15V3" />
    </>,
  ),
  plus: make(<path d="M12 5v14M5 12h14" />),
  close: make(<path d="M18 6 6 18M6 6l12 12" />),
  chevron: make(<path d="m9 18 6-6-6-6" />),
  clock: make(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>,
  ),
  sliders: make(<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" />),
  check: make(<path d="M20 6 9 17l-5-5" />, 3),
  "arrow-right": make(<path d="M5 12h14M13 6l6 6-6 6" />),
};
