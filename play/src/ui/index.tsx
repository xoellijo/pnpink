import React, { useEffect, useRef, useState } from 'react';
import { GameElement, ProfileBadge, render } from '@boardzilla/core';
import setup, { manifest, PlayGame, PlayPiece, PlayPlayer, PlayZone, reorderActionName } from '../game/index.js';
import type { AreaDefinition, Box } from '../manifest.js';
import { canChoose, chooseElement, chooseSequence, contextMenu, ContextAction, hasPendingAction, submitChoice } from './context-menu.js';
import { clearSelected, elementAtBranch, selectedElements, setSelected, toggleSelected, useSelectedElements } from './multiselect.js';
import { openPreview, PreviewOverlay, setPreviewCandidate } from './preview.js';
import { HoverShortcuts, KeyboardShortcuts, useHoverShortcuts } from './shortcuts.js';
import './style.scss';

if (!document.querySelector('script[src*="iconify"]')) {
  const iconify = document.createElement('script');
  iconify.src = 'https://code.iconify.design/2/2.1.2/iconify.min.js';
  iconify.defer = true;
  document.head.appendChild(iconify);
}

function useContextTrigger(open: (x: number, y: number) => void, tap?: () => void) {
  const timer = useRef<number>();
  const origin = useRef({ x: 0, y: 0 });
  const moved = useRef(false);
  const longPressed = useRef(false);
  const blockClick = useRef(false);
  const cancel = () => {
    if (timer.current !== undefined) window.clearTimeout(timer.current);
    timer.current = undefined;
  };
  return {
    onContextMenu: (event: React.MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      open(event.clientX, event.clientY);
    },
    onPointerDown: (event: React.PointerEvent) => {
      if (event.pointerType !== 'touch') return;
      origin.current = { x: event.clientX, y: event.clientY };
      moved.current = false;
      longPressed.current = false;
      timer.current = window.setTimeout(() => {
        longPressed.current = true;
        blockClick.current = true;
        navigator.vibrate?.(18);
        open(event.clientX, event.clientY);
      }, 500);
    },
    onPointerMove: (event: React.PointerEvent) => {
      if (Math.abs(event.clientX - origin.current.x) + Math.abs(event.clientY - origin.current.y) > 6) {
        moved.current = true;
        cancel();
      }
    },
    onPointerUp: (event: React.PointerEvent) => {
      const isTap = event.pointerType === 'touch' && !moved.current && !longPressed.current;
      cancel();
      if (isTap && tap) {
        blockClick.current = true;
        tap();
      }
    },
    onPointerCancel: () => {
      moved.current = true;
      cancel();
    },
    consumeClick: () => {
      const consume = blockClick.current;
      blockClick.current = false;
      return consume;
    },
  };
}

function ZoneView({ zone, viewer }: { zone: PlayZone; viewer: PlayPlayer }) {
  const owner = zone.ownerPosition >= 0
    ? zone.game.players.find(player => player.position === zone.ownerPosition)
    : undefined;
  const isOpponentHand = zone.kind === 'hand' && owner?.position !== viewer.position;
  const { consumeClick: consumeZoneClick, ...contextHandlers } = useContextTrigger(function showZoneActions(x, y) {
    const actions: ContextAction[] = [];
    const node = elementNode(zone);
    const zoneInfo: ContextAction | undefined = node ? {
      label: 'Info',
      icon: 'griddy-icons:info-circle',
      run: () => openPreview({ label: zoneSummary(zone), node }),
    } : undefined;
    const top = zone.top(PlayPiece);
    const stackInfo = top ? pieceInfoAction(top, viewer) : undefined;
    const selectTop = topSelectionActions(zone, stackInfo, () => queueMicrotask(() => showZoneActions(x, y)));
    if (selectTop) actions.push(selectTop);
    else if (zoneInfo) actions.push(zoneInfo);
    const stackOperations = stackOperationActions(zone, viewer);
    actions.push(...stackOperations);
    if (!stackOperations.length && canChoose(zone, 'shuffle')) {
      actions.push({ label: 'Shuffle', icon: 'mdi:shuffle-variant', run: () => chooseElement(zone, 'shuffle') });
    }
    if (canChoose(zone, 'draw')) {
      const targets = zone.game.drawTargets(viewer);
      actions.push({
        label: 'Draw',
        icon: 'mdi:cards',
        children: targets.map(target => ({
          label: target.label,
          icon: zoneIcon(target),
          run: () => chooseSequence('draw', zone, target),
        })),
      });
    }
    if ((zone.kind === 'center' || zone.kind === 'table') && hasPendingAction('takeTrick')) {
      actions.push({
        label: 'Take trick',
        icon: 'mdi:cards-playing',
        children: [...zone.game.players].map(player => ({
          label: player.name,
          icon: 'griddy-icons:user-circle',
          run: () => submitChoice('takeTrick', { winner: player.position }),
        })),
      });
    }
    if (actions.length) contextMenu.show({ title: zone.label, x, y, actions });
  });
  void consumeZoneClick;
  const hoverShortcuts = shortcutsForZone(zone);
  useHoverShortcuts(zone.branch(), hoverShortcuts);
  return <div
    className={`play-zone zone-${zone.kind} ${isOpponentHand ? 'opponent-hand' : ''}`}
    title={zoneSummary(zone)}
    data-pnpink-branch={zone.branch()}
    onPointerEnter={event => setPreviewCandidate({
      label: zoneSummary(zone),
      node: event.currentTarget.closest<HTMLElement>('.transform-wrapper') ?? event.currentTarget,
    })}
    onPointerLeave={() => setPreviewCandidate()}
    {...contextHandlers}
  >
    <span className="zone-label">{owner ? owner.name : zone.label}</span>
    <span className="zone-watermark iconify" data-icon={zoneIcon(zone)} aria-hidden="true" />
    {isOpponentHand && <strong>{zone.all(PlayPiece).length} cards</strong>}
  </div>;
}

function zoneSummary(zone: PlayZone): string {
  const owner = zone.ownerPosition >= 0
    ? zone.game.players.find(player => player.position === zone.ownerPosition)
    : undefined;
  const label = owner ? `${zone.label} of ${owner.name}` : zone.label;
  return `${label}: ${zone.all(PlayPiece).length} items`;
}

function elementNode(element: GameElement): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-pnpink-branch]'))
    .find(node => node.dataset.pnpinkBranch === element.branch())
    ?.closest<HTMLElement>('.transform-wrapper') ?? undefined;
}

function assetUrl(path: string): string {
  return path ? new URL(path, window.location.href.replace(/ui\.html$/, '')).href : '';
}

function zoneIcon(zone: PlayZone): string {
  if (zone.kind === 'discard' || zone.definitionId === 'discard') return 'griddy-icons:trash';
  if (zone.definitionId === 'tricks') return 'griddy-icons:trophy';
  if (zone.kind === 'center' || zone.kind === 'table') return 'griddy-icons:grid-alt';
  if (zone.kind === 'deck' || zone.definitionId === 'deck') return 'griddy-icons:layers';
  if (zone.kind === 'hand') return 'mdi:hand-back-right-outline';
  if (zone.ownerPosition >= 0) return 'griddy-icons:user-circle';
  return 'griddy-icons:component-card';
}

function isStackedZone(zone: PlayZone): boolean {
  const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId);
  const rows = definition?.layout.rows ?? 1;
  const columns = definition?.layout.columns ?? 1;
  return zone.all(PlayPiece).length > 1 && rows * columns === 1;
}

function shortcutsForZone(zone: PlayZone): HoverShortcuts {
  return {
    shuffle: canChoose(zone, 'shuffle') ? () => chooseElement(zone, 'shuffle') : undefined,
    selectTop: isStackedZone(zone)
      ? count => setSelected(count ? [...zone.topN(count, PlayPiece)] : [...zone.all(PlayPiece)])
      : undefined,
  };
}

function pieceInfoAction(piece: PlayPiece, viewer: PlayPlayer): ContextAction | undefined {
  const visible = piece.isVisibleTo(viewer);
  const source = visible && piece.faceUp || !piece.back ? piece.front : piece.back;
  return source ? {
    label: 'Info',
    icon: 'griddy-icons:info-circle',
    iconOnly: true,
    run: () => openPreview({ label: visible ? piece.label : 'Hidden component', source: assetUrl(source) }),
  } : undefined;
}

function topSelectionActions(zone: PlayZone, info?: ContextAction, refresh?: () => void): ContextAction | undefined {
  if (!isStackedZone(zone)) return undefined;
  const size = zone.all(PlayPiece).length;
  const counts: Array<number | 'all'> = Array.from(
    { length: Math.max(0, Math.min(10, size) - 1) },
    (_, index) => index + 2,
  );
  if (size > 10) counts.push('all');
  const selected = selectedElements().filter(candidate => candidate instanceof PlayPiece && candidate.container() === zone);
  return {
    label: 'Select top cards',
    compactRow: true,
    row: [...(info ? [info] : []), ...counts.map(count => ({
      label: count === 'all' ? 'All' : String(count),
      active: selected.length === (count === 'all' ? zone.all(PlayPiece).length : Math.min(count, zone.all(PlayPiece).length)),
      run: () => {
        setSelected(count === 'all' ? [...zone.all(PlayPiece)] : [...zone.topN(count, PlayPiece)]);
        refresh?.();
      },
    }))],
  };
}

function stackOperationActions(zone: PlayZone, viewer: PlayPlayer): ContextAction[] {
  if (!isStackedZone(zone)) return [];
  const pieces = [...zone.all(PlayPiece)];
  const top = zone.top(PlayPiece);
  if (!top) return [];
  const flippable = new Set(zone.game.flippablePieces(viewer));
  const canFlipTop = hasPendingAction('flip') && flippable.has(top);
  const allHaveBacks = pieces.every(piece => Boolean(piece.back));
  const canFlipAll = canFlipTop && allHaveBacks && pieces.every(piece => flippable.has(piece));
  const canSetAll = hasPendingAction('setFace') && canFlipAll;
  const flipActions: ContextAction[] = [];
  if (canFlipTop) {
    flipActions.push({
      label: 'Flip',
      icon: 'mdi:flip-horizontal',
      run: () => chooseElement(top, 'flip'),
    });
  }
  const topFaceUp = top.faceUp;
  if (canSetAll) {
    flipActions.push({
      label: topFaceUp ? 'Back all' : 'Front all',
      icon: topFaceUp ? 'mdi:eye-off-outline' : 'mdi:eye-outline',
      run: () => submitChoice('setFace', { piece: top, pieces, faceUp: !topFaceUp }),
    });
  }
  const canFlipDeck = hasPendingAction('flipDeck') && zone.game.flippableStacks(viewer).includes(zone);
  if (canFlipDeck) {
    flipActions.push({
      label: 'Flip deck',
      icon: 'mdi:cards-outline',
      run: () => submitChoice('flipDeck', { target: zone }),
    });
  }
  const pileActions: ContextAction[] = [];
  const canShuffle = hasPendingAction('shuffle') && zone.game.shuffleTargets(viewer).includes(zone);
  if (canShuffle) {
    pileActions.push({
      label: 'Shuffle',
      icon: 'mdi:shuffle-variant',
      run: () => submitChoice('shuffle', { target: zone }),
    });
  }
  const canDeal = hasPendingAction('deal') && zone.game.dealSources(viewer).includes(zone);
  if (canDeal) {
    const maximum = zone.game.maximumDealCount(zone);
    for (let count = 1; count <= maximum; count += 1) {
      pileActions.push({
        label: count === 1 ? 'Deal' : String(count),
        run: () => submitChoice('deal', { source: zone, count }),
      });
    }
  }
  return [
    ...(flipActions.length ? [{ label: 'Flip actions', row: flipActions }] : []),
    ...(pileActions.length ? [{ label: 'Pile actions', row: pileActions }] : []),
  ];
}

function selectedPieceGroup(piece: PlayPiece): PlayPiece[] {
  const selected = selectedElements().filter(candidate => candidate instanceof PlayPiece) as PlayPiece[];
  return selected.length > 1 && selected.includes(piece) ? selected : [piece];
}

function flipSelection(piece: PlayPiece): void {
  const group = selectedPieceGroup(piece);
  if (!group.every(candidate => canChoose(candidate, 'flip'))) return;
  if (group.length === 1) chooseElement(piece, 'flip');
  else submitChoice('flip', { piece, pieces: group });
}

function PieceView({ piece, viewer }: { piece: PlayPiece; viewer: PlayPlayer }) {
  const selected = useSelectedElements();
  const zone = piece.game.zoneOf(piece)!;
  const selectedPieces = selected.filter(candidate => candidate instanceof PlayPiece) as PlayPiece[];
  const selectedIndex = selectedPieces.indexOf(piece);
  const stackedSelection = selectedPieces.length > 1
    && selectedIndex >= 0
    && isStackedZone(zone)
    && selectedPieces.every(candidate => piece.game.zoneOf(candidate) === zone);
  const selectionOffset = stackedSelection ? selectedPieces.length - selectedIndex - 1 : 0;
  const selectionStyle = stackedSelection ? {
    '--selection-x': `${selectionOffset * .18}vmin`,
    '--selection-y': `${-1.2 - selectionOffset * .32}vmin`,
  } as React.CSSProperties : undefined;
  const visible = piece.isVisibleTo(viewer);
  const source = visible && piece.faceUp || !piece.back ? piece.front : piece.back;
  const showActions = (x: number, y: number) => {
    const group = selectedPieceGroup(piece);
    const actions: ContextAction[] = [];
    const info = pieceInfoAction(piece, viewer);
    const selectTop = topSelectionActions(zone, info, () => queueMicrotask(() => showActions(x, y)));
    if (selectTop) actions.push(selectTop);
    const stackOperations = stackOperationActions(zone, viewer);
    actions.push(...stackOperations);
    if (!selectTop && info) {
      actions.push({
        label: 'Info',
        icon: 'griddy-icons:info-circle',
        run: info.run,
      });
    }
    if (!stackOperations.length && group.every(candidate => canChoose(candidate, 'flip'))) {
      actions.push({ label: group.length > 1 ? `Flip ${group.length}` : 'Flip', icon: 'mdi:flip-horizontal', run: () => flipSelection(piece) });
    }
    if (canChoose(piece, 'move')) {
      const targets = piece.game.moveTargetsForPieces(viewer, group);
      actions.push({
        label: group.length > 1 ? `Move ${group.length} to` : 'Move to',
        row: [{
          label: group.length > 1 ? `Move ${group.length} to` : 'Move to',
          icon: 'griddy-icons:arrow-right',
          run: () => chooseElement(piece, 'move'),
        }, ...targets.map(target => ({
          label: `Move to ${target.label}`,
          icon: zoneIcon(target),
          iconOnly: true,
          run: () => group.length > 1
            ? submitChoice('move', { piece, pieces: group, target })
            : chooseSequence('move', piece, target),
        }))],
      });
    }
    if (group.every(candidate => canChoose(candidate, 'discard'))) {
      actions.push({
        label: group.length > 1 ? `Discard ${group.length}` : 'Discard',
        icon: 'mdi:delete-outline',
        children: piece.game.discardTargets().map(target => ({
          label: target.label,
          icon: zoneIcon(target),
          run: () => group.length > 1
            ? submitChoice('discard', { piece, pieces: group, target })
            : chooseSequence('discard', piece, target),
        })),
      });
    }
    if (actions.length) contextMenu.show({ title: '', x, y, actions });
  };
  const selectable = canChoose(piece, 'move') || canChoose(piece, reorderActionName(zone));
  const hoverShortcuts: HoverShortcuts = {
    ...shortcutsForZone(zone),
    flip: canChoose(piece, 'flip') ? () => flipSelection(piece) : undefined,
  };
  useHoverShortcuts(piece.branch(), hoverShortcuts);
  const { consumeClick, ...contextHandlers } = useContextTrigger(showActions, () => {
    if (selectable) setSelected([piece]);
  });
  return <div
    className={`play-piece ${visible && piece.faceUp ? 'face-up' : 'face-down'}`}
    title={visible ? `${piece.label} · ${zoneSummary(zone)}` : zoneSummary(zone)}
    data-pnpink-branch={piece.branch()}
    data-pnpink-selectable={selectable ? 'true' : 'false'}
    data-pnpink-stack-selection={stackedSelection ? 'true' : undefined}
    style={selectionStyle}
    onPointerEnter={() => {
      if (source) setPreviewCandidate({ label: visible ? piece.label : 'Hidden component', source: assetUrl(source) });
    }}
    onPointerLeave={() => {
      setPreviewCandidate();
    }}
    onClickCapture={event => {
      if (consumeClick()) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
        event.stopPropagation();
        if (selectable) toggleSelected(piece);
        return;
      }
      const selected = selectedElements();
      if (selected.length > 1 && selected.includes(piece)) {
        event.preventDefault();
        event.stopPropagation();
        showActions(event.clientX, event.clientY);
      } else if (selected.length) {
        clearSelected();
      }
    }}
    {...contextHandlers}
  >
    {source ? <img draggable={false} src={assetUrl(source)} alt={visible ? piece.label : 'Hidden component'} /> : <strong>{visible ? piece.label : '?'}</strong>}
    {stackedSelection && selectedIndex === 0 && <span className="selection-count">{selectedPieces.length} selected</span>}
  </div>;
}

interface SelectionRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

function useLassoSelection(): SelectionRect | undefined {
  const [rectangle, setRectangle] = useState<SelectionRect>();

  useEffect(() => {
    let origin: { x: number; y: number } | undefined;
    let additive: GameElement[] = [];

    const update = (event: PointerEvent) => {
      if (!origin) return;
      event.preventDefault();
      const left = Math.min(origin.x, event.clientX);
      const top = Math.min(origin.y, event.clientY);
      const right = Math.max(origin.x, event.clientX);
      const bottom = Math.max(origin.y, event.clientY);
      const next = { left, top, width: right - left, height: bottom - top };
      setRectangle(next);
      if (next.width + next.height < 6) return;
      const enclosed = Array.from(document.querySelectorAll<HTMLElement>('[data-pnpink-selectable="true"]'))
        .filter(node => {
          const box = node.getBoundingClientRect();
          return box.left < right && box.right > left && box.top < bottom && box.bottom > top;
        })
        .map(node => elementAtBranch(node.dataset.pnpinkBranch ?? ''))
        .filter((element): element is GameElement => Boolean(element));
      setSelected([...additive, ...enclosed]);
    };

    const finish = () => {
      origin = undefined;
      setRectangle(undefined);
      window.removeEventListener('pointermove', update);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
    };

    const start = (event: PointerEvent) => {
      if (event.button !== 0 || (event.target as Element).closest('.play-piece, .lil-gui, button, input, select')) return;
      origin = { x: event.clientX, y: event.clientY };
      additive = event.ctrlKey || event.metaKey ? selectedElements() : [];
      if (!additive.length) clearSelected();
      window.addEventListener('pointermove', update, { passive: false });
      window.addEventListener('pointerup', finish);
      window.addEventListener('pointercancel', finish);
    };

    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        finish();
        clearSelected();
      }
    };

    window.addEventListener('pointerdown', start, true);
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('pointerdown', start, true);
      window.removeEventListener('keydown', keydown);
      finish();
    };
  }, []);

  return rectangle;
}

function relativePosition(zone: PlayZone, viewer: PlayPlayer): number {
  const count = zone.game.players.length;
  return (zone.ownerPosition - viewer.position + count) % count;
}

function opponentBox(relative: number, count: number, portrait: boolean): Box {
  if (count === 2) return portrait
    ? { left: 27, top: 2, width: 46, height: 12 }
    : { left: 34, top: 2, width: 32, height: 14 };
  if (relative === 1) return portrait
    ? { left: 2, top: 17, width: 24, height: 15 }
    : { left: 2, top: 8, width: 19, height: 18 };
  if (relative === count - 1) return portrait
    ? { left: 74, top: 17, width: 24, height: 15 }
    : { left: 79, top: 8, width: 19, height: 18 };
  return portrait
    ? { left: 27, top: 2, width: 46, height: 12 }
    : { left: 34, top: 2, width: 32, height: 14 };
}

function zoneBox(zone: PlayZone, viewer: PlayPlayer, boardSize: string): Box {
  const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId)!;
  const portrait = boardSize === 'portrait';
  if (definition.scope === 'shared') {
    if (!portrait) return definition.layout.shared!;
    if (zone.kind === 'center' || zone.kind === 'table') return { left: 17, top: 27, width: 66, height: 44 };
    if (zone.kind === 'deck' || zone.definitionId === 'deck') return { left: 2, top: 37, width: 14, height: 22 };
    if (zone.kind === 'discard' || zone.definitionId === 'discard') return { left: 84, top: 37, width: 14, height: 22 };
    return definition.layout.shared!;
  }
  if (zone.kind === 'hand') {
    if (zone.ownerPosition === viewer.position) return portrait
      ? { left: 2, top: 77, width: 96, height: 21 }
      : { left: 12, top: 76, width: 76, height: 22 };
    return opponentBox(relativePosition(zone, viewer), zone.game.players.length, portrait);
  }
  if (zone.definitionId === 'tricks') {
    if (zone.ownerPosition === viewer.position) return portrait
      ? { left: 84, top: 78, width: 13, height: 18 }
      : { left: 90, top: 76, width: 8, height: 20 };
    const opponent = opponentBox(relativePosition(zone, viewer), zone.game.players.length, portrait);
    return { left: opponent.left + opponent.width - 5, top: opponent.top + 2, width: 5, height: Math.max(8, opponent.height - 4) };
  }
  return zone.ownerPosition === viewer.position ? definition.layout.owner! : definition.layout.opponent!;
}

function pieceLayout(definition: AreaDefinition) {
  if (definition.kind === 'hand') return {
    rows: 1,
    columns: { min: 1 },
    direction: 'ltr' as const,
    alignment: 'center' as const,
    scaling: 'fill' as const,
    maxOverlap: 72,
    gap: 0.25,
  };
  return {
    rows: definition.layout.rows ?? { min: 1 },
    columns: definition.layout.columns ?? { min: 1 },
    margin: definition.layout.gap ?? 0.45,
  };
}

function trickSlot(piece: PlayPiece, viewer: PlayPlayer): Box {
  const count = piece.game.players.length;
  const relative = (piece.playedByPosition - viewer.position + count) % count;
  if (relative === 0) return { left: 40, top: 61, width: 20, height: 34 };
  if (relative === 1) return { left: 8, top: 33, width: 20, height: 34 };
  if (relative === count - 1) return { left: 72, top: 33, width: 20, height: 34 };
  return { left: 40, top: 5, width: 20, height: 34 };
}

function Table({ game, viewer }: { game: PlayGame; viewer: PlayPlayer }) {
  const current = game.players.current();
  const selectionRectangle = useLassoSelection();
  const playerColors = Object.fromEntries([...game.players].map((player, index) => [`--player-${index + 1}`, player.color]));
  useEffect(() => {
    const gameRoot = document.querySelector<HTMLElement>('#game');
    if (!gameRoot) return;
    for (const [property, color] of Object.entries(playerColors)) gameRoot.style.setProperty(property, String(color));
    return () => {
      for (const property of Object.keys(playerColors)) gameRoot.style.removeProperty(property);
    };
  }, [game]);
  return <main className="play-table" style={{ background: manifest.board.background, ...playerColors } as React.CSSProperties}>
    <header><div><h1>{manifest.name}</h1><p>{manifest.description}</p></div><div className="turn-status">{game.trickComplete ? 'Trick complete' : current ? `${current.name}'s turn` : 'Free play'}</div></header>
    <footer><ProfileBadge player={viewer} /> Drag a highlighted card or tap it to play.</footer>
    <PreviewOverlay />
    <KeyboardShortcuts />
    {selectionRectangle && <div className="selection-lasso" style={selectionRectangle} />}
  </main>;
}

render(setup, {
  boardSizes: [
    { name: 'landscape', aspectRatio: manifest.board.aspectRatio, orientation: 'landscape', scaling: 'fit' },
    { name: 'portrait', aspectRatio: 0.62, orientation: 'portrait', scaling: 'fit' },
  ],
  layout: (game, viewer, boardSize) => {
    game.appearance({ className: 'pnpink-play-root', render: current => <Table game={current} viewer={viewer} /> });
    game.all(PlayZone).appearance({ render: zone => <ZoneView zone={zone} viewer={viewer} /> });
    game.all(PlayPiece).appearance({ aspectRatio: 0.7, render: piece => <PieceView piece={piece} viewer={viewer} /> });
    for (const zone of game.all(PlayZone)) {
      const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId)!;
      game.layout(zone, { slots: [zoneBox(zone, viewer, boardSize)] });
      delete (zone as unknown as { layout?: unknown }).layout;
      if (zone.kind === 'center' || zone.kind === 'table') {
        for (const piece of zone.all(PlayPiece)) zone.layout(piece, { slots: [trickSlot(piece, viewer)] });
      } else {
        zone.layout(PlayPiece, pieceLayout(definition));
      }
    }
    game.layoutControls({ element: game, position: 'inset', left: 30, top: 44, width: 40, height: 12 });
  },
  infoModals: [{
    title: 'Generic table',
    modal: () => <div><h2>Manifest-driven play</h2><p>This table was built entirely from <code>play.json</code>. Draw, move, discard, flip and shuffle components, then end your turn.</p></div>,
  }],
});
