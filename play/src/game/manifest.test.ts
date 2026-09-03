import { describe, expect, it } from 'vitest';
import rawManifest from '../../play.json';
import { PlayManifest, validateManifest } from '../manifest.js';

describe('play manifest', () => {
  it('accepts the generic table example', () => {
    expect(validateManifest(rawManifest as PlayManifest).zones).toHaveLength(5);
  });

  it('rejects unknown initial zones', () => {
    const invalid = structuredClone(rawManifest) as PlayManifest;
    invalid.components[0].initial.zone = 'missing';
    expect(() => validateManifest(invalid)).toThrow('Unknown initial zone');
  });
});
