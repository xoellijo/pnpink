import { build } from 'esbuild';
import { sassPlugin } from 'esbuild-sass-plugin';

await build({
  entryPoints: ['src/game/game-interface.ts'],
  bundle: true,
  format: 'iife',
  globalName: 'game',
  outdir: 'build/game',
  target: 'es2020',
  keepNames: true,
  loader: { '.jpg': 'file', '.png': 'file', '.svg': 'file', '.webp': 'file', '.scss': 'css', '.ogg': 'dataurl' },
  plugins: [sassPlugin()],
});
