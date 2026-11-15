/** Anatomy copy for the Today spine figure: one name and one sentence per drawn vertebra. */
export interface VertebraInfo {
  readonly name: string;
  readonly title: string;
  readonly text: string;
}

const CERVICAL_TEXT: Readonly<Record<number, string>> = {
  1: 'The atlas: a ring that carries the skull and lets the head nod.',
  2: 'The axis: its odontoid peg is the pivot that lets the head turn.',
  3: 'Part of the mobile neck; the spinal cord is measured from about here.',
  4: 'Part of the mobile neck, supporting the weight and movement of the head.',
  5: 'Part of the mobile neck; the cervical spine curves gently forward here.',
  6: 'Part of the mobile neck, with a prominent front tubercle for neck muscles.',
  7: 'The vertebra prominens: its long spinous process is the bump at the base of the neck.',
};

const THORACIC_TEXT: Readonly<Record<number, string>> = {
  1: 'The first rib-bearing vertebra, at the base of the neck; it articulates with the first rib.',
  2: 'Articulates with a pair of ribs near the top of the rib cage.',
  3: 'Articulates with a pair of ribs and lies level with the top of the shoulder blade.',
  4: 'Articulates with a pair of ribs and helps form the rib cage.',
  5: 'Articulates with a pair of ribs as the thoracic curve bends gently backward.',
  6: 'Articulates with a pair of ribs and holds the chest wall steady.',
  7: 'Articulates with a pair of ribs near the apex of the thoracic curve.',
  8: 'Articulates with a pair of ribs in the middle of the rib cage.',
  9: 'Articulates with a pair of ribs and carries the lower rib cage.',
  10: 'Articulates with a single pair of ribs, the tenth.',
  11: 'Carries a floating rib that does not reach the breastbone.',
  12: 'Carries the last floating rib and marks the change to the lumbar spine.',
};

const LUMBAR_TEXT: Readonly<Record<number, string>> = {
  1: 'The first rib-free vertebra; the lumbar vertebrae bear most of the body weight.',
  2: 'Bears most of the body weight and allows bending forward and back.',
  3: 'Bears most of the body weight; the middle of the lumbar curve.',
  4: 'Bears most of the body weight and moves a lot in everyday bending.',
  5: 'The lowest and largest mobile vertebra; it carries the load down to the sacrum.',
};

export const cervicalInfo = (level: number): VertebraInfo => ({
  name: `C${String(level)}`,
  title: `C${String(level)} cervical vertebra`,
  text: CERVICAL_TEXT[level] ?? '',
});

export const thoracicInfo = (level: number): VertebraInfo => ({
  name: `T${String(level)}`,
  title: `T${String(level)} thoracic vertebra`,
  text: THORACIC_TEXT[level] ?? '',
});

export const lumbarInfo = (level: number): VertebraInfo => ({
  name: `L${String(level)}`,
  title: `L${String(level)} lumbar vertebra`,
  text: LUMBAR_TEXT[level] ?? '',
});

export const sacrumInfo: VertebraInfo = {
  name: 'Sacrum',
  title: 'Sacrum',
  text: 'Five fused vertebrae that join the spine to the pelvis.',
};

export const coccyxInfo: VertebraInfo = {
  name: 'Coccyx',
  title: 'Coccyx',
  text: 'The tailbone: a few small fused vertebrae at the base of the spine.',
};

export const vertebraLabel = (info: VertebraInfo): string => `${info.title}. ${info.text}`;
