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
    buildContext.onLoad({ filter: /@boardzilla[\\/]core[\\/]entry[\\/]ui[\\/]store\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        [
          'const position = s.position || update.position;',
          'const position = update.position ?? s.position;',
        ],
        [
          "if (s.setup && (gameManager.phase === 'new' || update.state.sequence !== s.renderedSequence + 1)) {",
          "if (s.setup && (gameManager.phase === 'new' || update.state.sequence !== s.renderedSequence + 1 || s.position !== update.position)) {",
        ],
        [
          'if (update.state.sequence === s.renderedSequence + 1 && state.rendered)',
          'if (update.state.sequence === s.renderedSequence + 1 && s.position === update.position && state.rendered)',
        ],
        [
          'placement: undefined,\n        };',
          'placement: undefined,\n            selected: undefined,\n        };',
        ],
        [
          'const elementChosen = { [move.selections[0].name]: s.gameManager.game.atBranch(dragElement) };',
          `const draggedElement = s.gameManager.game.atBranch(dragElement);
                const selectedPieces = s.selected?.length > 1 && s.selected.some(candidate => candidate.branch() === draggedElement.branch()) ? s.selected : undefined;
                const elementChosen = {
                    [move.selections[0].name]: draggedElement,
                    ...(selectedPieces ? { pieces: selectedPieces } : {})
                };`,
        ],
        [
          'get().selectMove(move, { [selection.name]: element });',
          `const selectedPieces = move.name === 'move' && s.selected?.length > 1 && s.selected.some(candidate => candidate.branch() === element.branch())
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

const boardzillaPerspectivePlugin = {
  name: 'boardzilla-perspective',
  setup(buildContext) {
    buildContext.onLoad({ filter: /@boardzilla[\\/]core[\\/]entry[\\/]ui[\\/]Main\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        [
          'const [gameManager, updateState, setUserOnline, announcementIndex] = gameStore(s => [s.gameManager, s.updateState, s.setUserOnline, s.announcementIndex]);',
          'const [gameManager, updateState, setUserOnline, announcementIndex, position] = gameStore(s => [s.gameManager, s.updateState, s.setUserOnline, s.announcementIndex, s.position]);',
        ],
        [
          "React.createElement(Game, null)",
          "React.createElement(Game, { key: position ?? 'setup' })",
        ],
      ];
      for (const [before, after] of replacements) {
        if (!source.includes(before)) throw new Error('Boardzilla perspective patch no longer matches Main.js');
        source = source.replace(before, after);
      }
      return { contents: source, loader: 'js' };
    });
  },
};

const boardzillaDragPlugin = {
  name: 'boardzilla-drag-compatibility',
  setup(buildContext) {
    buildContext.onLoad({ filter: /@boardzilla[\\/]core[\\/]entry[\\/]ui[\\/]game[\\/]components[\\/]Element\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        ['if (!isMobile && element instanceof Piece) {', 'if (element instanceof Piece) {'],
        [
          's.selected?.includes(element) || Object.values(s.move?.args || {}).some',
          's.selected?.some(candidate => candidate.branch() === element.branch()) || Object.values(s.move?.args || {}).some',
        ],
        ['const domElement = useRef(null);', `const domElement = useRef(null);
    const touchDrop = useRef({ active: false, target: undefined });
    const dragGroupOffsets = useRef(new Map());
    const fullStackSource = useRef(undefined);
    const singleDragSelection = useRef(false);`],
        [
          `const handleDragStart = useCallback((e, data) => {
        e.stopPropagation();`,
          `const handleDragStart = useCallback((e, data) => {
        e.stopPropagation();
        const selected = gameStore.getState().selected ?? [];
        if (!(selected.length > 1 && selected.some(candidate => candidate.branch() === element.branch()))) {
            gameStore.setState({ selected: [element] });
            singleDragSelection.current = true;
        } else {
            singleDragSelection.current = false;
        }
        const fullStackPiece = wrapper.current?.querySelector('[data-pnpink-stack-all-selected="true"]');
        fullStackSource.current = fullStackPiece ? wrapper.current?.closest('.Space') : undefined;`,
        ],
        ['if (dragging && styles.transform) {', "if (dragging && styles.top !== undefined && styles.left !== undefined) {"],
        ['if (styles.transform && dragOffset.ref === element._t.ref) {', 'if (dragOffset.ref === element._t.ref) {'],
        ['` + styles.transform', "` + (styles.transform ?? '')"],
        [
          `wrapper.current.setAttribute('data-lasty', String(data.lastY));`,
          `wrapper.current.setAttribute('data-lasty', String(data.lastY));
            dragGroupOffsets.current.clear();
            const selected = gameStore.getState().selected ?? [];
            if (selected.length > 1 && selected.some(candidate => candidate.branch() === element.branch())) {
                const anchorRect = wrapper.current.getBoundingClientRect();
                selected.filter(candidate => candidate.branch() !== element.branch()).forEach((candidate, index) => {
                    const peer = document.getElementById(candidate.name)?.parentElement;
                    if (!peer?.classList.contains('transform-wrapper')) return;
                    const peerRect = peer.getBoundingClientRect();
                    const depth = Math.min(index + 1, 7);
                    dragGroupOffsets.current.set(candidate.name, {
                        x: anchorRect.left - peerRect.left + depth * Math.max(1.5, anchorRect.width * 0.04),
                        y: anchorRect.top - peerRect.top + depth * Math.max(.8, anchorRect.height * 0.02),
                        z: 99 - depth
                    });
                });
            }`,
        ],
        [
          'setDragging({ deltaY, deltaX });',
          `setDragging({ deltaY, deltaX });
                for (const selected of gameStore.getState().selected ?? []) {
                    if (selected.branch() === element.branch()) continue;
                    const peer = document.getElementById(selected.name)?.parentElement;
                    if (!peer?.classList.contains('transform-wrapper')) continue;
                    const offset = dragGroupOffsets.current.get(selected.name) ?? { x: 0, y: 0, z: 98 };
                    peer.classList.add('group-dragging');
                    peer.style.translate = \`\${offset.x - deltaX}px \${offset.y - deltaY}px\`;
                    peer.style.setProperty('z-index', String(offset.z), 'important');
                }
                fullStackSource.current?.classList.add('pnpink-full-stack-drag');
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
            wrapper.current.style.removeProperty('z-index');
        }
        if (fullStackSource.current) {
            fullStackSource.current.classList.remove('pnpink-full-stack-drag');
            fullStackSource.current = undefined;
        }
        dragGroupOffsets.current.clear();
        delete render.mutated;`,
        ],
        [
          'setDragging(undefined);\n            setCurrentDrop(undefined);',
          `for (const selected of gameStore.getState().selected ?? []) {
                const peer = document.getElementById(selected.name)?.parentElement;
                peer?.classList.remove('group-dragging');
                peer?.style.removeProperty('translate');
                peer?.style.removeProperty('z-index');
            }
            touchDrop.current = { active: false, target: undefined };
            dragGroupOffsets.current.clear();
            fullStackSource.current?.classList.remove('pnpink-full-stack-drag');
            fullStackSource.current = undefined;
            dragOffset.ref = undefined;
            dragOffset.x = undefined;
            dragOffset.y = undefined;
            if (singleDragSelection.current) {
                const draggedBranch = element.branch();
                window.setTimeout(() => {
                    const selected = gameStore.getState().selected ?? [];
                    if (selected.length === 1 && selected[0].branch() === draggedBranch) {
                        gameStore.setState({ selected: undefined });
                    }
                }, 650);
                singleDragSelection.current = false;
            }
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
    buildContext.onLoad({ filter: /@boardzilla[\\/]core[\\/]entry[\\/]ui[\\/]game[\\/]components[\\/]PlayerControls\.js$/ }, async ({ path: sourcePath }) => {
      let source = await readFile(sourcePath, 'utf8');
      const replacements = [
        [
          `if (!position || !controls)
        return null;
    if (!boardPrompt && controls.moves.length === 0)`,
          `if (!position || !controls)
        return null;
    const nativeControlMoves = ['takeTrick', 'endTurn'];
    const visibleMoves = controls.moves.filter(move => nativeControlMoves.some(name =>
        move.name === name || move.name.startsWith(name + ':') || move.name.endsWith(':' + name)
    ));
    if (visibleMoves.length === 0)`,
        ],
        ['controls.moves.map((pendingMove, i) => {', 'visibleMoves.map((pendingMove, i) => {'],
        [
          'className: `player-controls ${controls.name.replace(":", "-")}`',
          'className: `player-controls ${controls.name.replace(":", "-")} ${visibleMoves.some(move => move.name === "takeTrick" || move.name.startsWith("takeTrick:") || move.name.endsWith(":takeTrick")) ? "winner-controls" : ""}`',
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
  plugins: [boardzillaStorePlugin, boardzillaPerspectivePlugin, boardzillaDragPlugin, boardzillaControlsPlugin, sassPlugin()],
});
