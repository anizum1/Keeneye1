/**
 * The visual vocabulary, in one place.
 *
 * Colour is semantic here, not decorative: gold always means sealed evidence,
 * crimson always means a broken hash. An examiner should be able to read the
 * state of the vault from across the room without reading a word of it.
 */

export const PALETTE = {
  void:    0x050810,
  deep:    0x0a1020,
  cyan:    0x00d4ff,
  gold:    0xffb300,
  green:   0x00ff9d,
  crimson: 0xff2d55,
  violet:  0x7b2cff,
  bone:    0xdfe7f5,
  slate:   0x4a5878,
};

/** Roles, so scene code never reaches for a raw colour. */
export const ROLE = {
  background:   PALETTE.void,
  structure:    PALETTE.slate,
  active:       PALETTE.cyan,
  sealed:       PALETTE.gold,
  intact:       PALETTE.green,
  broken:       PALETTE.crimson,
  attachment:   PALETTE.violet,
  text:         PALETTE.bone,
};

export const GROUP_COLOUR = {
  case:       PALETTE.cyan,
  evidence:   PALETTE.gold,
  attachment: PALETTE.violet,
  examiner:   PALETTE.green,
};

/** Fog keeps the far regions of the world from reading as clutter. */
export const FOG = { color: PALETTE.void, near: 18, far: 190 };
