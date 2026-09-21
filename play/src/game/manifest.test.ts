import { describe, expect, it } from 'vitest';
import rawManifest from '../../play.json';
import { PlayManifest, validateManifest } from '../manifest.js';

describe('play manifest', () => {
  it('accepts the generic table example', () => {
    const manifest = validateManifest(rawManifest as PlayManifest);
    expect(manifest.areas).toHaveLength(10);
    expect(manifest.toolboxes).toHaveLength(2);
    expect(manifest.areas.find(area => area.id === 'shared-tokens')).toMatchObject({
      toolbox: 'general-tools',
      infinite: true,
    });
  });

  it('rejects unknown initial areas', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.pieces[0].initial.area = 'missing';
    expect(() => validateManifest(invalid)).toThrow('Unknown initial area');
  });

  it('rejects invalid drop limits', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.areas[1].maxDrop = 0;
    expect(() => validateManifest(invalid)).toThrow('invalid maxDrop');
  });

  it('rejects invalid insertion modes', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.areas[0].insert = 'middle' as 'top';
    expect(() => validateManifest(invalid)).toThrow('invalid insert mode');
  });

  it('rejects invalid pile capabilities', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.areas[0].allowDeal = 'yes' as unknown as boolean;
    expect(() => validateManifest(invalid)).toThrow('invalid allowDeal');
  });

  it('accepts trick play configuration', () => {
    const configured = structuredClone(rawManifest) as PlayManifest;
    configured.turns = {
      mode: 'trick-assisted',
      order: 'left',
      maxCardsPerPlay: 0,
      winnerDecider: 'all',
    };
    expect(validateManifest(configured).turns).toEqual(configured.turns);
  });

  it('rejects invalid trick play limits', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.turns = { mode: 'trick-assisted', maxCardsPerPlay: -1 };
    expect(() => validateManifest(invalid)).toThrow('maxCardsPerPlay');
  });

  it('validates opponent panel modes', () => {
    const configured = structuredClone(rawManifest) as PlayManifest;
    configured.players = { count: 4, panelMode: 'standard' };
    expect(validateManifest(configured).players?.panelMode).toBe('standard');
    configured.players.panelMode = 'sideways' as 'rotate';
    expect(() => validateManifest(configured)).toThrow('opponent panel mode');
  });

  it('resolves inherited types before validation', () => {
    const configured = {
      ...structuredClone(rawManifest),
      types: {
        areas: {
          base: { scope: 'shared', visibility: 'public', actions: ['move-from'], layout: { rows: 2 } },
          table: { extends: 'base', kind: 'table', layout: { shared: { left: 10, top: 10, width: 80, height: 80 } } },
        },
        pieces: { marker: { type: 'token', copies: 3 } },
      },
      areas: [{ id: 'board', extends: 'table', label: 'Board', actions: ['move-to'], layout: { shared: { width: 70 } } }],
      pieces: [{ id: 'marker', extends: 'marker', label: 'Marker', initial: { area: 'board' } }],
      setup: [],
    } as unknown as PlayManifest;
    const resolved = validateManifest(configured);
    expect(resolved.types).toBeUndefined();
    expect(resolved.areas[0]).toMatchObject({
      actions: ['move-to'],
      layout: { rows: 2, shared: { left: 10, top: 10, width: 70, height: 80 } },
    });
    expect(resolved.pieces[0]).toMatchObject({ type: 'token', copies: 3 });
  });

  it('accepts semantic component metadata and area filters', () => {
    const configured = structuredClone(rawManifest) as PlayManifest;
    configured.areas[0].accepts = {
      types: ['card'],
      roles: ['playing-card'],
      properties: { suit: 'hearts' },
    };
    configured.pieces[0].roles = ['playing-card'];
    configured.pieces[0].tags = ['royal'];
    configured.pieces[0].properties = { suit: 'hearts', strength: 13 };
    expect(validateManifest(configured).pieces[0]).toMatchObject({
      roles: ['playing-card'],
      properties: { suit: 'hearts', strength: 13 },
    });
  });

});
