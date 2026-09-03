export type ZoneScope = 'shared' | 'player';
export type ZoneVisibility = 'public' | 'owner' | 'hidden';
export type ZoneKind = 'table' | 'deck' | 'hand' | 'discard' | 'area' | 'supply';
export type ZoneAction = 'draw' | 'move-from' | 'move-to' | 'discard-to' | 'shuffle';

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface ZoneLayout {
  shared?: Box;
  owner?: Box;
  opponent?: Box;
  rows?: number;
  columns?: number;
  gap?: number;
}

export interface ZoneDefinition {
  id: string;
  label: string;
  kind: ZoneKind;
  scope: ZoneScope;
  visibility: ZoneVisibility;
  actions?: ZoneAction[];
  layout: ZoneLayout;
}

export interface ComponentDefinition {
  id: string;
  label: string;
  copies?: number;
  front: string;
  back?: string;
  initial: {
    zone: string;
    forEachPlayer?: boolean;
    faceUp?: boolean;
  };
}

export interface PlayManifest {
  schema: 1;
  name: string;
  description?: string;
  board: {
    aspectRatio: number;
    background?: string;
  };
  zones: ZoneDefinition[];
  components: ComponentDefinition[];
  setup?: {
    shuffle?: string[];
  };
}

export function validateManifest(value: PlayManifest): PlayManifest {
  if (value.schema !== 1) throw new Error(`Unsupported play manifest schema: ${value.schema}`);
  const zoneIds = new Set<string>();
  for (const zone of value.zones) {
    if (zoneIds.has(zone.id)) throw new Error(`Duplicate zone: ${zone.id}`);
    zoneIds.add(zone.id);
    if (zone.scope === 'shared' && !zone.layout.shared) throw new Error(`Shared zone ${zone.id} needs layout.shared`);
    if (zone.scope === 'player' && (!zone.layout.owner || !zone.layout.opponent)) {
      throw new Error(`Player zone ${zone.id} needs owner and opponent layouts`);
    }
  }
  for (const component of value.components) {
    const zone = value.zones.find(candidate => candidate.id === component.initial.zone);
    if (!zone) throw new Error(`Unknown initial zone ${component.initial.zone} for ${component.id}`);
    if (component.initial.forEachPlayer && zone.scope !== 'player') {
      throw new Error(`${component.id} uses forEachPlayer with shared zone ${zone.id}`);
    }
  }
  return value;
}
