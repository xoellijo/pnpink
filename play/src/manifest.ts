export type AreaScope = 'shared' | 'player';
export type AreaVisibility = 'public' | 'owner' | 'hidden';
export type AreaKind = 'hand' | 'stack' | 'center' | 'free' | 'table' | 'deck' | 'discard' | 'area' | 'supply';
export type AreaAction = 'draw' | 'move-from' | 'move-to' | 'discard-to' | 'shuffle';
export type TurnMode = 'free' | 'guided' | 'trick-assisted';

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface AreaLayout {
  shared?: Box;
  owner?: Box;
  opponent?: Box;
  rows?: number;
  columns?: number;
  gap?: number;
}

export interface AreaDefinition {
  id: string;
  label: string;
  kind: AreaKind;
  scope: AreaScope;
  visibility: AreaVisibility;
  actions?: AreaAction[];
  maxDrop?: number;
  layout: AreaLayout;
}

export interface PieceDefinition {
  id: string;
  label: string;
  copies?: number;
  front: string;
  back?: string;
  initial: {
    area: string;
    forEachPlayer?: boolean;
    faceUp?: boolean;
  };
}

export interface PlayManifest {
  schema: 1;
  name: string;
  description?: string;
  players?: {
    count?: number;
    min?: number;
    max?: number;
  };
  turns?: {
    mode: TurnMode;
    order?: 'clockwise' | 'counterclockwise';
  };
  board: {
    aspectRatio: number;
    background?: string;
  };
  areas: AreaDefinition[];
  pieces: PieceDefinition[];
  setup?: Array<
    { shuffle: string }
    | { deal: { from: string; to: string; count: number } }
  > | {
    shuffle?: string[];
  };
}

declare global {
  var __PNPINK_PLAY_MANIFEST__: PlayManifest | undefined;
}

export function currentManifest(): PlayManifest {
  const value = globalThis.__PNPINK_PLAY_MANIFEST__;
  if (!value) throw new Error('PnPInk Play manifest was not provided');
  return validateManifest(value);
}

export function validateManifest(value: PlayManifest): PlayManifest {
  if (value.schema !== 1) throw new Error(`Unsupported play manifest schema: ${value.schema}`);
  const areaIds = new Set<string>();
  for (const area of value.areas) {
    if (areaIds.has(area.id)) throw new Error(`Duplicate area: ${area.id}`);
    areaIds.add(area.id);
    if (area.scope === 'shared' && !area.layout.shared) throw new Error(`Shared area ${area.id} needs layout.shared`);
    if (area.scope === 'player' && (!area.layout.owner || !area.layout.opponent)) {
      throw new Error(`Player area ${area.id} needs owner and opponent layouts`);
    }
    if (area.maxDrop !== undefined && (!Number.isInteger(area.maxDrop) || area.maxDrop < 1)) {
      throw new Error(`Area ${area.id} has an invalid maxDrop`);
    }
  }
  for (const piece of value.pieces) {
    const area = value.areas.find(candidate => candidate.id === piece.initial.area);
    if (!area) throw new Error(`Unknown initial area ${piece.initial.area} for ${piece.id}`);
    if (piece.initial.forEachPlayer && area.scope !== 'player') {
      throw new Error(`${piece.id} uses forEachPlayer with shared area ${area.id}`);
    }
  }
  if (value.players?.count !== undefined && (value.players.min !== undefined || value.players.max !== undefined)) {
    throw new Error('players.count cannot be combined with players.min or players.max');
  }
  const playerMin = value.players?.count ?? value.players?.min ?? 2;
  const playerMax = value.players?.count ?? value.players?.max ?? playerMin;
  if (playerMin < 1 || playerMax < playerMin) throw new Error('Invalid player count');
  for (const operation of setupOperations(value)) {
    if ('shuffle' in operation && !areaIds.has(operation.shuffle)) throw new Error(`Unknown shuffle area ${operation.shuffle}`);
    if ('deal' in operation) {
      if (!areaIds.has(operation.deal.from)) throw new Error(`Unknown deal source ${operation.deal.from}`);
      const target = value.areas.find(area => area.id === operation.deal.to);
      if (!target || target.scope !== 'player') throw new Error(`Deal target ${operation.deal.to} must be a player area`);
      if (operation.deal.count < 1) throw new Error('Deal count must be positive');
    }
  }
  return value;
}

export function setupOperations(value: PlayManifest): Array<{ shuffle: string } | { deal: { from: string; to: string; count: number } }> {
  if (Array.isArray(value.setup)) return value.setup;
  return (value.setup?.shuffle ?? []).map(shuffle => ({ shuffle }));
}
