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

});
