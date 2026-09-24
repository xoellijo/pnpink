export type AreaScope = 'shared' | 'player';
export type AreaVisibility = 'public' | 'owner' | 'hidden';
export type AreaKind = 'hand' | 'stack' | 'center' | 'free' | 'table' | 'deck' | 'discard' | 'tricks' | 'tableau' | 'area' | 'supply';
export type AreaAction = 'draw' | 'move-from' | 'move-to' | 'discard-to' | 'shuffle' | 'deal';
export type AreaHideMode = 'toggle' | 'sync' | 'select';
export type ToolboxScope = 'shared' | 'player';
export type ToolboxSide = 'left' | 'right';
export type PieceType = 'card' | 'tile' | 'token' | 'die' | 'd6' | 'pawn' | 'meeple' | 'cube' | 'marker' | 'chip' | 'board' | 'mat' | 'area';
export type ResourceShape = 'circle' | 'square';
export type TurnMode = 'free' | 'guided' | 'trick-assisted';
export type TurnOrder = 'right' | 'left' | 'clockwise' | 'counterclockwise';
export type WinnerDecider = 'all' | 'last' | 'next-to-last' | 'host';
export type OpponentPanelMode = 'rotate' | 'standard';
export type UpdatePolicy = 'live' | 'new-games' | 'pinned';
export type UpdateMode = 'versioned' | 'latest-only';

const PIECE_TYPES: PieceType[] = ['card', 'tile', 'token', 'die', 'd6', 'pawn', 'meeple', 'cube', 'marker', 'chip', 'board', 'mat', 'area'];

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

export interface StackTabDefinition {
  showCount?: boolean;
  autoHide?: boolean;
}

export interface AreaDefinition {
  extends?: string | string[];
  id: string;
  label: string;
  kind: AreaKind;
  scope: AreaScope;
  visibility: AreaVisibility;
  actions?: AreaAction[];
  maxDrop?: number;
  insert?: 'top' | 'bottom';
  allowShuffle?: boolean;
  allowDeal?: boolean;
  canHide?: AreaHideMode;
  toolbox?: string;
  infinite?: boolean;
  stackTab?: StackTabDefinition;
  accepts?: PieceAcceptance;
  layout: AreaLayout;
}

export interface PieceAcceptanceFilter {
  types?: PieceType[];
  roles?: string[];
  tags?: string[];
  properties?: Record<string, unknown>;
}

export type PieceAcceptance = PieceType | PieceAcceptanceFilter;

export interface ToolboxDefinition {
  id: string;
  label: string;
  scope: ToolboxScope;
  side?: ToolboxSide;
}

export interface PieceDefinition {
  extends?: string | string[];
  id: string;
  label: string;
  type?: PieceType;
  copies?: number;
  front?: string;
  back?: string;
  color?: string;
  value?: string | number;
  roles?: string[];
  tags?: string[];
  properties?: Record<string, unknown>;
  shape?: ResourceShape;
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
  updates?: {
    mode: UpdateMode;
    assets: UpdatePolicy;
    components: UpdatePolicy;
    rules: UpdatePolicy;
  };
  players?: {
    count?: number;
    min?: number;
    max?: number;
    panelMode?: OpponentPanelMode;
  };
  turns?: {
    mode: TurnMode;
    order?: TurnOrder;
    maxCardsPerPlay?: number;
    winnerDecider?: WinnerDecider;
  };
  board: {
    aspectRatio: number;
    background?: string;
  };
  types?: {
    areas?: Record<string, Partial<AreaDefinition>>;
    pieces?: Record<string, Partial<PieceDefinition>>;
  };
  toolboxes?: ToolboxDefinition[];
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
  if (!value) throw new Error('PnPPlay manifest was not provided');
  return validateManifest(value);
}

export function validateManifest(value: PlayManifest): PlayManifest {
  value = resolveManifestTypes(value);
  if (value.schema !== 1) throw new Error(`Unsupported play manifest schema: ${value.schema}`);
  if (value.updates) {
    if (!['versioned', 'latest-only'].includes(value.updates.mode)) throw new Error('Invalid update mode');
    for (const key of ['assets', 'components', 'rules'] as const) {
      if (!['live', 'new-games', 'pinned'].includes(value.updates[key])) throw new Error(`Invalid ${key} update policy`);
    }
  }
  const areaIds = new Set<string>();
  const toolboxIds = new Set<string>();
  for (const toolbox of value.toolboxes ?? []) {
    if (toolboxIds.has(toolbox.id)) throw new Error(`Duplicate toolbox: ${toolbox.id}`);
    toolboxIds.add(toolbox.id);
    if (toolbox.side !== undefined && !['left', 'right'].includes(toolbox.side)) {
      throw new Error(`Toolbox ${toolbox.id} has an invalid side`);
    }
  }
  for (const area of value.areas) {
    if (areaIds.has(area.id)) throw new Error(`Duplicate area: ${area.id}`);
    areaIds.add(area.id);
    if (!area.toolbox && area.scope === 'shared' && !area.layout.shared) throw new Error(`Shared area ${area.id} needs layout.shared`);
    if (!area.toolbox && area.scope === 'player' && (!area.layout.owner || !area.layout.opponent)) {
      throw new Error(`Player area ${area.id} needs owner and opponent layouts`);
    }
    if (area.toolbox) {
      const toolbox = value.toolboxes?.find(candidate => candidate.id === area.toolbox);
      if (!toolbox) throw new Error(`Area ${area.id} uses unknown toolbox ${area.toolbox}`);
      if (toolbox.scope !== area.scope) throw new Error(`Area ${area.id} and toolbox ${area.toolbox} need the same scope`);
    }
    if (area.infinite !== undefined && typeof area.infinite !== 'boolean') {
      throw new Error(`Area ${area.id} has an invalid infinite value`);
    }
    if (area.stackTab !== undefined) {
      if (!area.stackTab || typeof area.stackTab !== 'object' || Array.isArray(area.stackTab)) {
        throw new Error(`Area ${area.id} has an invalid stackTab value`);
      }
      if (area.stackTab.showCount !== undefined && typeof area.stackTab.showCount !== 'boolean') {
        throw new Error(`Area ${area.id} has an invalid stackTab.showCount value`);
      }
      if (area.stackTab.autoHide !== undefined && typeof area.stackTab.autoHide !== 'boolean') {
        throw new Error(`Area ${area.id} has an invalid stackTab.autoHide value`);
      }
    }
    if (area.maxDrop !== undefined && (!Number.isInteger(area.maxDrop) || area.maxDrop < 1)) {
      throw new Error(`Area ${area.id} has an invalid maxDrop`);
    }
    if (area.insert !== undefined && !['top', 'bottom'].includes(area.insert)) {
      throw new Error(`Area ${area.id} has an invalid insert mode`);
    }
    if (area.allowShuffle !== undefined && typeof area.allowShuffle !== 'boolean') {
      throw new Error(`Area ${area.id} has an invalid allowShuffle value`);
    }
    if (area.allowDeal !== undefined && typeof area.allowDeal !== 'boolean') {
      throw new Error(`Area ${area.id} has an invalid allowDeal value`);
    }
    if (area.canHide !== undefined && !['toggle', 'sync', 'select'].includes(area.canHide)) {
      throw new Error(`Area ${area.id} has an invalid canHide mode`);
    }
    if (area.canHide !== undefined && area.scope !== 'player') {
      throw new Error(`Hideable area ${area.id} must have player scope`);
    }
    if (typeof area.accepts === 'string' && !PIECE_TYPES.includes(area.accepts)) {
      throw new Error(`Area ${area.id} accepts an invalid piece type`);
    }
    if (area.accepts && typeof area.accepts === 'object') {
      if (area.accepts.types?.some(type => !PIECE_TYPES.includes(type))) {
        throw new Error(`Area ${area.id} accepts an invalid piece type`);
      }
      if (area.accepts.roles?.some(role => typeof role !== 'string') || area.accepts.tags?.some(tag => typeof tag !== 'string')) {
        throw new Error(`Area ${area.id} has invalid semantic filters`);
      }
      if (area.accepts.properties !== undefined && (
        !area.accepts.properties || typeof area.accepts.properties !== 'object' || Array.isArray(area.accepts.properties)
      )) {
        throw new Error(`Area ${area.id} has invalid property filters`);
      }
    }
  }
  for (const piece of value.pieces) {
    const area = value.areas.find(candidate => candidate.id === piece.initial.area);
    if (!area) throw new Error(`Unknown initial area ${piece.initial.area} for ${piece.id}`);
    if (piece.initial.forEachPlayer && area.scope !== 'player') {
      throw new Error(`${piece.id} uses forEachPlayer with shared area ${area.id}`);
    }
    const type = piece.type ?? 'card';
    if (!PIECE_TYPES.includes(type)) {
      throw new Error(`${piece.id} has an invalid piece type`);
    }
    if (piece.shape !== undefined && !['circle', 'square'].includes(piece.shape)) {
      throw new Error(`${piece.id} has an invalid resource shape`);
    }
    if (['card', 'tile', 'board', 'mat', 'area'].includes(type) && !piece.front) {
      throw new Error(`${type} ${piece.id} needs a front image`);
    }
    if (piece.roles !== undefined && (!Array.isArray(piece.roles) || piece.roles.some(role => typeof role !== 'string'))) {
      throw new Error(`${piece.id} has invalid roles`);
    }
    if (piece.tags !== undefined && (!Array.isArray(piece.tags) || piece.tags.some(tag => typeof tag !== 'string'))) {
      throw new Error(`${piece.id} has invalid tags`);
    }
    if (piece.properties !== undefined && (!piece.properties || typeof piece.properties !== 'object' || Array.isArray(piece.properties))) {
      throw new Error(`${piece.id} has invalid properties`);
    }
  }
  if (value.players?.count !== undefined && (value.players.min !== undefined || value.players.max !== undefined)) {
    throw new Error('players.count cannot be combined with players.min or players.max');
  }
  const playerMin = value.players?.count ?? value.players?.min ?? 2;
  const playerMax = value.players?.count ?? value.players?.max ?? playerMin;
  if (playerMin < 1 || playerMax < playerMin) throw new Error('Invalid player count');
  if (value.players?.panelMode !== undefined && !['rotate', 'standard'].includes(value.players.panelMode)) {
    throw new Error(`Invalid opponent panel mode: ${value.players.panelMode}`);
  }
  if (
    value.turns?.order !== undefined
    && !['right', 'left', 'clockwise', 'counterclockwise'].includes(value.turns.order)
  ) {
    throw new Error(`Invalid turn order: ${value.turns.order}`);
  }
  if (
    value.turns?.maxCardsPerPlay !== undefined
    && (!Number.isInteger(value.turns.maxCardsPerPlay) || value.turns.maxCardsPerPlay < 0)
  ) {
    throw new Error('turns.maxCardsPerPlay must be zero or a positive integer');
  }
  if (
    value.turns?.winnerDecider !== undefined
    && !['all', 'last', 'next-to-last', 'host'].includes(value.turns.winnerDecider)
  ) {
    throw new Error(`Invalid winner decider: ${value.turns.winnerDecider}`);
  }
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

function mergeDefinitions<T extends object>(base: Partial<T>, override: Partial<T>): Partial<T> {
  const result = cloneValue(base) as Record<string, unknown>;
  for (const [key, value] of Object.entries(override)) {
    const current = result[key];
    result[key] = value && current && typeof value === 'object' && typeof current === 'object'
      && !Array.isArray(value) && !Array.isArray(current)
      ? mergeDefinitions(current, value)
      : cloneValue(value);
  }
  return result as Partial<T>;
}

function cloneValue<T>(value: T): T {
  if (Array.isArray(value)) return value.map(cloneValue) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneValue(item)])) as T;
  }
  return value;
}

function parentNames(value: string | string[] | undefined, context: string): string[] {
  if (value === undefined) return [];
  const names = typeof value === 'string' ? [value] : value;
  if (!Array.isArray(names) || names.some(name => typeof name !== 'string' || !name)) {
    throw new Error(`${context}.extends must be a type name or list of type names`);
  }
  return names;
}

function resolveDefinitions<T extends { extends?: string | string[] }>(
  items: T[],
  definitions: Record<string, Partial<T>> = {},
  category: string,
): T[] {
  const resolved = new Map<string, Partial<T>>();
  const resolving = new Set<string>();
  const resolveType = (name: string): Partial<T> => {
    const cached = resolved.get(name);
    if (cached) return cached;
    const definition = definitions[name];
    if (!definition) throw new Error(`Unknown ${category} type: ${name}`);
    if (resolving.has(name)) throw new Error(`Circular ${category} type inheritance: ${name}`);
    resolving.add(name);
    let result: Partial<T> = {};
    for (const parent of parentNames(definition.extends, `${category} type ${name}`)) {
      result = mergeDefinitions(result, resolveType(parent));
    }
    const { extends: _extends, ...own } = definition;
    result = mergeDefinitions(result, own as Partial<T>);
    resolving.delete(name);
    resolved.set(name, result);
    return result;
  };
  Object.keys(definitions).forEach(resolveType);
  return items.map((item, index) => {
    let result: Partial<T> = {};
    for (const parent of parentNames(item.extends, `${category}[${index + 1}]`)) {
      result = mergeDefinitions(result, resolveType(parent));
    }
    const { extends: _extends, ...own } = item;
    return mergeDefinitions(result, own as Partial<T>) as T;
  });
}

export function resolveManifestTypes(value: PlayManifest): PlayManifest {
  const resolved = cloneValue(value);
  resolved.areas = resolveDefinitions(resolved.areas, resolved.types?.areas, 'area');
  if (Array.isArray(resolved.pieces)) {
    resolved.pieces = resolveDefinitions(resolved.pieces, resolved.types?.pieces, 'piece');
  }
  delete resolved.types;
  return resolved;
}

export function setupOperations(value: PlayManifest): Array<{ shuffle: string } | { deal: { from: string; to: string; count: number } }> {
  if (Array.isArray(value.setup)) return value.setup;
  return (value.setup?.shuffle ?? []).map(shuffle => ({ shuffle }));
}
