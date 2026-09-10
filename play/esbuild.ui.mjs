import { build } from 'esbuild';
import { sassPlugin } from 'esbuild-sass-plugin';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const boardzillaStorePlugin = {
  name: 'boardzilla-store',
  setup(buildContext) {
    buildContext.onResolve({ filter: /^pnpink-boardzilla-store$/ }, () => ({
      path: path.resolve('node_modules/@boardzilla/core/entry/ui/store.js'),
    }));
    buildContext.onLoad({ filter: /@boardzilla\/core\/entry\/ui\/store\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        [
          'placement: undefined,\n        };',
          'placement: undefined,\n            selected: undefined,\n        };',
        ],
        [
          'const elementChosen = { [move.selections[0].name]: s.gameManager.game.atBranch(dragElement) };',
          `const draggedElement = s.gameManager.game.atBranch(dragElement);
                const selectedPieces = s.selected?.length > 1 && s.selected.includes(draggedElement) ? s.selected : undefined;
                const elementChosen = {
                    [move.selections[0].name]: draggedElement,
                    ...(selectedPieces ? { pieces: selectedPieces } : {})
                };`,
        ],
        [
          'get().selectMove(move, { [selection.name]: element });',
          `const selectedPieces = move.name === 'move' && s.selected?.length > 1 && s.selected.includes(element)
                ? s.selected
                : undefined;
            get().selectMove(move, {
                [selection.name]: element,
                ...(selectedPieces ? { pieces: selectedPieces } : {})
            });`,
        ],
      ];
      for (const [before, after] of replacements) {
        if (!source.includes(before)) throw new Error('Boardzilla multiselect store patch no longer matches store.js');
        source = source.replace(before, after);
      }
      return { contents: source, loader: 'js' };
    });
  },
};

const boardzillaDragPlugin = {
  name: 'boardzilla-drag-compatibility',
  setup(buildContext) {
    buildContext.onLoad({ filter: /@boardzilla\/core\/entry\/ui\/game\/components\/Element\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        ['if (!isMobile && element instanceof Piece) {', 'if (element instanceof Piece) {'],
        ['const domElement = useRef(null);', `const domElement = useRef(null);
    const touchDrop = useRef({ active: false, target: undefined });
    const dragGroupOffsets = useRef(new Map());`],
        ['if (dragging && styles.transform) {', "if (dragging && styles.top !== undefined && styles.left !== undefined) {"],
        ['if (styles.transform && dragOffset.ref === element._t.ref) {', 'if (dragOffset.ref === element._t.ref) {'],
        ['` + styles.transform', "` + (styles.transform ?? '')"],
        [
          `wrapper.current.setAttribute('data-lasty', String(data.lastY));`,
          `wrapper.current.setAttribute('data-lasty', String(data.lastY));
            dragGroupOffsets.current.clear();
            const selected = gameStore.getState().selected ?? [];
            if (selected.length > 1 && selected.includes(element)) {
                const anchorRect = wrapper.current.getBoundingClientRect();
                selected.filter(candidate => candidate !== element).forEach((candidate, index) => {
                    const peer = document.getElementById(candidate.name)?.parentElement;
                    if (!peer?.classList.contains('transform-wrapper')) return;
                    const peerRect = peer.getBoundingClientRect();
                    dragGroupOffsets.current.set(candidate.name, {
                        x: anchorRect.left - peerRect.left + (index + 1) * anchorRect.width * 0.15,
                        y: anchorRect.top - peerRect.top + (index + 1) * anchorRect.height * 0.04
                    });
                });
            }`,
        ],
        [
          'setDragging({ deltaY, deltaX });',
          `setDragging({ deltaY, deltaX });
                for (const selected of gameStore.getState().selected ?? []) {
                    if (selected === element) continue;
                    const peer = document.getElementById(selected.name)?.parentElement;
                    if (!peer?.classList.contains('transform-wrapper')) continue;
                    const offset = dragGroupOffsets.current.get(selected.name) ?? { x: 0, y: 0 };
                    peer.classList.add('group-dragging');
                    peer.style.translate = \`\${offset.x - deltaX}px \${offset.y - deltaY}px\`;
                }
                const touch = e.touches?.[0] ?? e.changedTouches?.[0];
                if (touch) {
                    const dropNode = document.elementsFromPoint(touch.clientX, touch.clientY)
                        .map(candidate => candidate.closest?.('.droppable'))
                        .find(candidate => candidate && !wrapper.current?.contains(candidate));
                    const branchNode = dropNode?.querySelector?.('[data-pnpink-branch]');
                    const nextDrop = branchNode
                        ? gameStore.getState().gameManager.game.atBranch(branchNode.dataset.pnpinkBranch)
                        : undefined;
                    touchDrop.current = { active: true, target: nextDrop };
                    setCurrentDrop(nextDrop);
                }`,
        ],
        [
          'if (dragging) {\n            if (currentDrop) {',
          `if (dragging) {
            const resolvedDrop = touchDrop.current.active ? touchDrop.current.target : currentDrop;
            if (resolvedDrop) {`,
        ],
        ['c.choice === currentDrop', 'c.choice === resolvedDrop'],
        ['onSelectElement([move], currentDrop);', 'onSelectElement([move], resolvedDrop);'],
        [
          'selectable,\n            droppable',
          `selectable,
            droppable,
            'drag-pass-through': element instanceof Piece && !!dragElement && dragElement !== branch && !droppable,
            'current-drop': droppable && currentDrop === element`,
        ],
        [
          `className: classNames("transform-wrapper", { dragging, placing, 'has-info': !!info })`,
          `className: classNames("transform-wrapper", {
            dragging,
            placing,
            'has-info': !!info,
            'drag-pass-through': element instanceof Piece && !!dragElement && dragElement !== branch && !droppable
        })`,
        ],
        [
          'delete render.mutated;',
          `if (wrapper.current?.classList.contains('group-dragging')) {
            wrapper.current.classList.remove('group-dragging');
            wrapper.current.style.removeProperty('translate');
        }
        delete render.mutated;`,
        ],
        [
          'setDragging(undefined);\n            setCurrentDrop(undefined);',
          `for (const selected of gameStore.getState().selected ?? []) {
                const peer = document.getElementById(selected.name)?.parentElement;
                peer?.classList.remove('group-dragging');
                peer?.style.removeProperty('translate');
            }
            touchDrop.current = { active: false, target: undefined };
            dragGroupOffsets.current.clear();
            setDragging(undefined);
            setCurrentDrop(undefined);`,
        ],
      ];
      for (const [before, after] of replacements) {
        if (!source.includes(before)) throw new Error('Boardzilla drag compatibility patch no longer matches Element.js');
        source = source.replace(before, after);
      }
      return { contents: source, loader: 'js' };
    });
  },
};

const boardzillaControlsPlugin = {
  name: 'boardzilla-controls',
  setup(buildContext) {
    buildContext.onLoad({ filter: /@boardzilla\/core\/entry\/ui\/game\/components\/PlayerControls\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        [
          `if (!position || !controls)
        return null;
    if (!boardPrompt && controls.moves.length === 0)`,
          `if (!position || !controls)
        return null;
    const visibleMoves = controls.moves.filter(move => ![
        'draw', 'move', 'discard', 'flip', 'setFace', 'flipDeck', 'shuffle', 'deal'
    ].includes(move.name) && !move.name.startsWith('reorder-'));
    if (visibleMoves.length === 0)`,
        ],
        ['controls.moves.map((pendingMove, i) => {', 'visibleMoves.map((pendingMove, i) => {'],
        [
          'className: `player-controls ${controls.name.replace(":", "-")}`',
          'className: `player-controls ${controls.name.replace(":", "-")} ${visibleMoves.some(move => move.name === "takeTrick") ? "winner-controls" : ""}`',
        ],
      ];
      for (const [before, after] of replacements) {
        if (!source.includes(before)) throw new Error('Boardzilla controls patch no longer matches PlayerControls.js');
        source = source.replace(before, after);
      }
      return { contents: source, loader: 'js' };
    });
  },
};

await build({
  entryPoints: ['src/ui/index.tsx'],
  bundle: true,
  format: 'iife',
  outfile: 'build/ui/index.js',
  target: 'es2020',
  keepNames: true,
  assetNames: 'assets/[name]-[hash]',
  loader: { '.svg': 'file', '.png': 'file', '.jpg': 'file', '.jpeg': 'file', '.webp': 'file', '.ttf': 'file', '.scss': 'css', '.ogg': 'dataurl' },
  plugins: [boardzillaStorePlugin, boardzillaDragPlugin, boardzillaControlsPlugin, sassPlugin()],
});
