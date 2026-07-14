// Shared brand palette + type scale for the app, matching the website
// (css/styles.css custom properties) and the admin dashboard (Tailwind
// theme) so all three surfaces read as one product.

export const colors = {
  green900: "#0f2a1b",
  green800: "#163827",
  green700: "#1b4332",
  green500: "#2e5339",
  gold600: "#b5872f",
  gold500: "#c99a3d",
  gold300: "#e7c581",
  cream50: "#faf6ef",
  cream100: "#f3ecdf",
  cream200: "#ebe1cd",
  ink900: "#211d17",
  ink700: "#3a3428",
  ink600: "#5c5646",
  ink400: "#8a836f",
  red600: "#a8402c",
  white: "#ffffff",
} as const;

// Semantic aliases: green is the structural/brand accent color (tab bar,
// spinners, links), gold is the "make this happen" primary-action color
// (submit/save/assign buttons) -- the same two-tone split used across the
// website's CTA buttons and the admin dashboard's Button component.
export const theme = {
  primary: colors.green700,
  primaryDark: colors.green900,
  accent: colors.gold600,
  accentLight: colors.gold500,
  background: colors.cream50,
  surface: colors.white,
  border: colors.cream200,
  text: colors.ink900,
  textMuted: colors.ink600,
  danger: colors.red600,
} as const;

export const fonts = {
  display: "Fraunces_600SemiBold",
  displayBold: "Fraunces_700Bold",
  body: "Inter_400Regular",
  bodyMedium: "Inter_500Medium",
  bodySemiBold: "Inter_600SemiBold",
} as const;
