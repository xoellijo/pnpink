import { describe, expect, it } from 'vitest';
import rawManifest from '../../play.json';
import { PlayManifest, validateManifest } from '../manifest.js';

describe('play manifest', () => {
  it('accepts the generic table example', () => {
    expect(validateManifest(rawManifest as PlayManifest).areas).toHaveLength(6);
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

});
