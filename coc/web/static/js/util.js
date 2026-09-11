/** Small helpers shared across scenes. */

export const TAU = Math.PI * 2;

export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export const lerp = (a, b, t) => a + (b - a) * t;

/** Frame-rate independent approach toward a target. */
export const damp = (current, target, lambda, dt) =>
  lerp(current, target, 1 - Math.exp(-lambda * dt));

export const easeInOutCubic = (t) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

export const easeOutBack = (t) => {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
};

/**
 * A small deterministic PRNG (mulberry32).
 *
 * Layouts are seeded from a case number so the same case always produces the
 * same web. "Chaotic but harmonious" has to be reproducible, or an examiner
 * could never say "the node on the left" twice.
 */
export function seededRandom(seed) {
  let a = seed >>> 0;
  return function random() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Turn any string into a 32-bit seed. */
export function hashSeed(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * Bytes of a hex digest as numbers in 0..1.
 *
 * Used to derive geometry from a hash — the cut of the login key, the profile
 * of a sealed evidence slab — so that the shape genuinely encodes the digest
 * rather than merely being decorated with it.
 */
export function digestToUnits(hex, count) {
  const units = [];
  const source = (hex || '').replace(/[^0-9a-f]/gi, '') || '0';
  for (let index = 0; index < count; index += 1) {
    const at = (index * 2) % source.length;
    const pair = source.slice(at, at + 2).padEnd(2, '0');
    units.push(parseInt(pair, 16) / 255);
  }
  return units;
}

export const humanBytes = (value) => {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? size : size.toFixed(1)} ${units[unit]}`;
};

/** Shorten a long filename from the middle, keeping the extension visible. */
export const truncate = (text, limit = 28) => {
  const value = String(text ?? '');
  if (value.length <= limit) return value;
  const head = Math.ceil((limit - 1) / 2);
  return `${value.slice(0, head)}…${value.slice(-(limit - head - 1))}`;
};

export const shortHash = (hex, span = 8) =>
  hex ? `${hex.slice(0, span)}…${hex.slice(-span)}` : '—';

/** Escape text destined for innerHTML. Hashes are safe; filenames are not. */
export const escapeHtml = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

export const formatStamp = (iso) => {
  if (!iso) return '—';
  return String(iso).replace('T', ' ').replace('Z', ' UTC');
};
