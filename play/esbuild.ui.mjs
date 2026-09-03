import { build } from 'esbuild';
import { sassPlugin } from 'esbuild-sass-plugin';
import { cp, mkdir } from 'node:fs/promises';

await build({
  entryPoints: ['src/ui/index.tsx'],
  bundle: true,
  format: 'iife',
  outfile: 'build/ui/index.js',
  target: 'es2020',
  keepNames: true,
  assetNames: 'assets/[name]-[hash]',
  loader: { '.svg': 'file', '.png': 'file', '.jpg': 'file', '.jpeg': 'file', '.webp': 'file', '.ttf': 'file', '.scss': 'css', '.ogg': 'dataurl' },
  plugins: [sassPlugin()],
});

await mkdir('build/ui/assets', { recursive: true });
await cp('src/assets', 'build/ui/assets', { recursive: true });
