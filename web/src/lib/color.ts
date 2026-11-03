// WCAG contrast helpers. The API already lightens team colours that are too dark for the
// dark surface; in light mode the opposite can happen (Mercedes teal, Racing Bulls yellow
// on white), so those get darkened here until they reach 3:1.

const LIGHT_SURFACE = "#FFFFFF";
const MIN_GRAPHIC_CONTRAST = 3;

function rgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Mix a colour with black in 5% steps until it reaches 3:1 on the light surface. */
export function darkenForLight(hex: string): string {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return hex;
  const [r, g, b] = rgb(hex);
  for (let step = 0; step <= 20; step++) {
    const k = 1 - step / 20;
    const mixed = `#${[r, g, b].map((c) => Math.round(c * k).toString(16).padStart(2, "0")).join("")}`;
    if (contrast(mixed, LIGHT_SURFACE) >= MIN_GRAPHIC_CONTRAST) return mixed;
  }
  return "#000000";
}
