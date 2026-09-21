import React, { useEffect, useRef, useState } from 'react';
import { GameElement, Piece, ProfileBadge, render } from '@boardzilla/core';
import { Flippable, useD6 } from '@boardzilla/core/components';
import setup, { areaIsConcealed, areaIsConcealedFrom, areaItems, isPlayItem, manifest, PlayArea, PlayDestination, PlayDie, PlayGame, PlayItem, PlayPiece, PlayPlayer, PlayPlayerPanel, PlayResource, PlayStack, PlayToolbox, PlayZone, reorderActionName } from '../game/index.js';
import type { AreaDefinition, Box } from '../manifest.js';
import { beginChoice, canChoose, chooseElement, chooseSequence, contextMenu, ContextAction, hasPendingAction, submitChoice, useCanChoose } from './context-menu.js';
import { clearSelected, elementAtBranch, selectedElements, setSelected, toggleSelected, useSelectedElements } from './multiselect.js';
import { openPreview, PreviewOverlay, setPreviewCandidate, usePreviewCandidate } from './preview.js';
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

function ZoneView({ zone, viewer }: { zone: PlayArea; viewer: PlayPlayer }) {
  const owner = zone.ownerPosition >= 0
    ? zone.game.players.find(player => player.position === zone.ownerPosition)
    : undefined;
  const isOpponentHand = zone.kind === 'hand' && owner?.position !== viewer.position;
  const isOpponentSubarea = owner?.position !== viewer.position
    && (zone.kind === 'hand' || zone.kind === 'tricks' || zone.kind === 'tableau');
  const isMoveTarget = useCanChoose(zone, 'move');
  const isConcealedOwner = owner?.position === viewer.position && areaIsConcealed(zone);
  const isConcealedViewer = areaIsConcealedFrom(zone, viewer.position);
  const previewItem = isConcealedViewer
    ? undefined
    : { label: zoneSummary(zone), content: <AreaPreview area={zone} viewer={viewer} /> };
  usePreviewCandidate(zone.branch(), previewItem);
  const { consumeClick: consumeZoneClick, ...contextHandlers } = useContextTrigger(function showZoneActions(x, y) {
    const actions: ContextAction[] = [];
    const node = elementNode(zone);
    const zoneInfo: ContextAction | undefined = previewItem || node ? {
      label: 'Info',
      icon: 'griddy-icons:info-circle',
      run: () => openPreview(previewItem ?? { label: zoneSummary(zone), node }),
    } : undefined;
    const top = zone.top(Piece);
    const stackInfo = top instanceof PlayPiece ? pieceInfoAction(top, viewer) : undefined;
    const selectTop = topSelectionActions(zone, stackInfo, () => queueMicrotask(() => showZoneActions(x, y)));
    if (selectTop) actions.push(selectTop);
    else if (zoneInfo) actions.push(zoneInfo);
    const visibilityAction = areaVisibilityAction(zone, viewer);
    if (visibilityAction) actions.push(visibilityAction);
    const stackOperations = stackOperationActions(zone, viewer);
    actions.push(...stackOperations);
    if (top instanceof PlayDie && canChoose(top, 'roll')) {
      actions.push({ label: 'Roll', icon: 'mdi:dice-6-outline', run: () => chooseElement(top, 'roll') });
    }
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
  const hoverShortcuts = shortcutsForZone(zone);
  useHoverShortcuts(zone.branch(), hoverShortcuts);
  const itemCount = areaItems(zone).length;
  const quantity = zone.infinite ? '' : ` (${itemCount})`;
  return <div
    className={`play-zone zone-${zone.kind} zone-${zone.definitionId} ${isOpponentHand ? 'opponent-hand' : ''} ${isOpponentSubarea ? 'opponent-subarea' : ''} ${isMoveTarget ? 'move-target' : ''} ${isConcealedOwner ? 'concealed-owner' : ''} ${isConcealedViewer ? 'concealed-viewer' : ''}`}
    title={isConcealedViewer ? 'Hidden area' : zoneSummary(zone)}
    data-pnpink-branch={zone.branch()}
    onPointerEnter={event => setPreviewCandidate(isConcealedViewer ? undefined : previewItem ?? {
      label: zoneSummary(zone),
      node: event.currentTarget.closest<HTMLElement>('.transform-wrapper') ?? event.currentTarget,
    })}
    onPointerLeave={() => setPreviewCandidate()}
    onClickCapture={event => {
      consumeZoneClick();
      if (canChoose(zone, 'move')) return;
      event.preventDefault();
      event.stopPropagation();
    }}
    {...contextHandlers}
  >
    <span className="zone-tag">
      <span className="zone-tag-icon iconify" data-icon={zoneIcon(zone)} aria-hidden="true" />
      {!zone.infinite && <span className="zone-tag-count">{itemCount}</span>}
      <span className="zone-tag-label">{zoneDisplayLabel(zone)}{quantity}</span>
    </span>
    {!isOpponentSubarea && <span className="zone-watermark iconify" data-icon={zoneIcon(zone)} aria-hidden="true" />}
  </div>;
}

function ToolboxView({ toolbox }: { toolbox: PlayToolbox }) {
  const isMoveTarget = useCanChoose(toolbox, 'move');
  return <section
    className={`play-toolbox toolbox-${toolbox.scope} ${isMoveTarget ? 'move-target' : ''}`}
    title={`${toolbox.label}: return resources to their matching supply`}
    data-pnpink-branch={toolbox.branch()}
    onClick={() => {
      if (canChoose(toolbox, 'move')) chooseElement(toolbox, 'move');
    }}
  >
    <span className="toolbox-icon iconify" data-icon={zoneIcon(toolbox)} aria-hidden="true" />
    <span className="toolbox-label">{toolbox.label}</span>
  </section>;
}

function areaVisibilityAction(zone: PlayArea, viewer: PlayPlayer): ContextAction | undefined {
  if (!zone.canHide || zone.ownerPosition !== viewer.position || !hasPendingAction('concealArea')) return undefined;
  if (zone.canHide === 'toggle') {
    const hidden = zone.concealedFor.length > 0;
    return {
      label: hidden ? 'Show area' : 'Hide area',
      icon: hidden ? 'mdi:eye-outline' : 'mdi:eye-off-outline',
      run: () => submitChoice('concealArea', { zone, operation: hidden ? 'show' : 'hide' }),
    };
  }
  if (zone.canHide === 'sync') {
    return {
      label: zone.syncHidden ? 'Ready to show' : areaIsConcealed(zone) ? 'Hide again' : 'Hide all matching areas',
      icon: zone.syncHidden ? 'mdi:eye-outline' : 'mdi:eye-off-outline',
      active: zone.syncHidden,
      run: () => submitChoice('concealArea', { zone, operation: zone.syncHidden ? 'show' : 'hide' }),
    };
  }
  const others = [...zone.game.players].filter(player => player.position !== viewer.position);
  const allHidden = others.length > 0 && others.every(player => zone.concealedFor.includes(player.position));
  return {
    label: 'Visibility',
    icon: 'mdi:account-eye-outline',
    children: [
      {
        label: allHidden ? 'Show to everyone' : 'Hide from everyone',
        icon: allHidden ? 'mdi:eye-outline' : 'mdi:eye-off-outline',
        active: allHidden,
        run: () => submitChoice('concealArea', { zone, operation: 'all' }),
      },
      ...others.map(player => {
        const hidden = zone.concealedFor.includes(player.position);
        return {
          label: hidden ? `Show to ${player.name}` : `Hide from ${player.name}`,
          icon: hidden ? 'mdi:eye-off-outline' : 'mdi:eye-outline',
          active: hidden,
          run: () => submitChoice('concealArea', { zone, operation: `player:${player.position}` }),
        };
      }),
    ],
  };
}

function zoneDisplayLabel(zone: PlayArea): string {
  return zone.kind === 'tableau' ? 'TABLEAU' : zone.label;
}

function contrastingTextColor(color: string): string {
  const hex = color.match(/^#([0-9a-f]{6})$/i)?.[1];
  if (!hex) return '#ffffff';
  const red = Number.parseInt(hex.slice(0, 2), 16);
  const green = Number.parseInt(hex.slice(2, 4), 16);
  const blue = Number.parseInt(hex.slice(4, 6), 16);
  return (red * 299 + green * 587 + blue * 114) / 1000 > 155 ? '#071b17' : '#ffffff';
}

function zoneSummary(zone: PlayArea): string {
  const owner = zone.ownerPosition >= 0
    ? zone.game.players.find(player => player.position === zone.ownerPosition)
    : undefined;
  const displayLabel = zoneDisplayLabel(zone);
  const label = owner ? `${displayLabel} of ${owner.name}` : displayLabel;
  return `${label}: ${areaItems(zone).length} items`;
}

function elementNode(element: GameElement): HTMLElement | undefined {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-pnpink-branch]'))
    .find(node => node.dataset.pnpinkBranch === element.branch())
    ?.closest<HTMLElement>('.transform-wrapper') ?? undefined;
}

function assetUrl(path: string): string {
  return path ? new URL(path, window.location.href.replace(/ui\.html$/, '')).href : '';
}

function pieceAssets(piece: PlayPiece): { front: string; back: string } {
  const definition = manifest.pieces.find(candidate => candidate.id === piece.componentId);
  return {
    front: definition?.front ?? piece.front,
    back: definition?.back ?? piece.back,
  };
}

function visiblePieceSource(piece: PlayPiece, viewer: PlayPlayer): string {
  const assets = pieceAssets(piece);
  return piece.isVisibleTo(viewer) && piece.faceUp || !assets.back ? assets.front : assets.back;
}

function zoneIcon(zone: PlayDestination): string {
  if (zone instanceof PlayToolbox) return zone.scope === 'shared' ? 'mdi:toolbox-outline' : 'mdi:account-wrench-outline';
  if (zone.kind === 'discard') return 'griddy-icons:trash';
  if (zone.kind === 'tricks') return 'griddy-icons:trophy';
  if (zone.kind === 'center' || zone.kind === 'table') return 'griddy-icons:grid-alt';
  if (zone.kind === 'deck') return 'griddy-icons:layers';
  if (zone.kind === 'hand') return 'mdi:hand-back-right-outline';
  if (zone.ownerPosition >= 0) return 'griddy-icons:user-circle';
  return 'griddy-icons:component-card';
}

function isStackedZone(zone: PlayArea): boolean {
  const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId);
  const rows = definition?.layout.rows ?? 1;
  const columns = definition?.layout.columns ?? 1;
  return areaItems(zone).length > 1 && rows * columns === 1;
}

function topAreaItems(zone: PlayArea, count = areaItems(zone).length): PlayItem[] {
  return [...zone.topN(count, Piece)].filter(isPlayItem);
}

function shortcutsForZone(zone: PlayArea): HoverShortcuts {
  return {
    shuffle: canChoose(zone, 'shuffle') ? () => chooseElement(zone, 'shuffle') : undefined,
    selectTop: isStackedZone(zone)
      ? count => {
        setSelected(count ? topAreaItems(zone, count) : areaItems(zone));
      }
      : undefined,
  };
}

function pieceInfoAction(piece: PlayPiece, viewer: PlayPlayer): ContextAction | undefined {
  const visible = piece.isVisibleTo(viewer);
  const source = visiblePieceSource(piece, viewer);
  return source ? {
    label: 'Info',
    icon: 'griddy-icons:info-circle',
    iconOnly: true,
    run: () => openPreview({ label: visible ? piece.label : 'Hidden component', source: assetUrl(source) }),
  } : undefined;
}

function topSelectionActions(zone: PlayArea, info?: ContextAction, refresh?: () => void): ContextAction | undefined {
  if (!isStackedZone(zone)) return undefined;
  const items = areaItems(zone);
  const size = items.length;
  const counts: Array<number | 'all'> = Array.from(
    { length: Math.max(0, Math.min(10, size) - 1) },
    (_, index) => index + 2,
  );
  if (size > 10) counts.push('all');
  const selected = selectedElements().filter(candidate => isPlayItem(candidate) && candidate.container() === zone);
  return {
    label: 'Select top cards',
    compactRow: true,
    row: [...(info ? [info] : []), ...counts.map(count => ({
      label: count === 'all' ? 'All' : String(count),
      active: selected.length === (count === 'all' ? items.length : Math.min(count, items.length)),
      run: () => {
        setSelected(count === 'all' ? items : topAreaItems(zone, count));
        refresh?.();
      },
    }))],
  };
}

function stackOperationActions(zone: PlayArea, viewer: PlayPlayer): ContextAction[] {
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
        label: count === 1 ? 'Deal 1' : String(count),
        run: () => submitChoice('deal', { source: zone, count }),
      });
    }
  }
  return [
    ...(flipActions.length ? [{ label: 'Flip actions', row: flipActions }] : []),
    ...(pileActions.length ? [{ label: 'Pile actions', row: pileActions }] : []),
  ];
}

function selectedPieceGroup(piece: PlayItem): PlayItem[] {
  const selected = selectedElements().filter(isPlayItem);
  return selected.length > 1 && selected.some(candidate => candidate.branch() === piece.branch()) ? selected : [piece];
}

function flipSelection(piece: PlayPiece): void {
  const group = selectedPieceGroup(piece).filter((candidate): candidate is PlayPiece => candidate instanceof PlayPiece);
  if (!group.every(candidate => canChoose(candidate, 'flip'))) return;
  if (group.length === 1) chooseElement(piece, 'flip');
  else submitChoice('flip', { piece, pieces: group });
}

function PieceView({ piece, viewer }: { piece: PlayItem; viewer: PlayPlayer }) {
  const selected = useSelectedElements();
  const zone = piece.game.zoneOf(piece)!;
  const selectedPieces = selected.filter(isPlayItem);
  const selectedIndex = selectedPieces.findIndex(candidate => candidate.branch() === piece.branch());
  const stackedSelection = selectedPieces.length > 1
    && selectedIndex >= 0
    && isStackedZone(zone)
    && selectedPieces.every(candidate => piece.game.zoneOf(candidate)?.branch() === zone.branch());
  const selectionOffset = stackedSelection ? selectedPieces.length - selectedIndex - 1 : 0;
  const selectionStyle = stackedSelection ? {
    '--selection-x': `${selectionOffset * .18}vmin`,
    '--selection-y': `${-1.2 - selectionOffset * .32}vmin`,
  } as React.CSSProperties : undefined;
  const selectionAnchor = stackedSelection && selectedIndex === 0;
  const visible = piece.isVisibleTo(viewer);
  const assets = piece instanceof PlayPiece ? pieceAssets(piece) : { front: '', back: '' };
  const source = piece instanceof PlayPiece ? visiblePieceSource(piece, viewer) : '';
  const front = assets.front ? assetUrl(assets.front) : '';
  const back = assets.back ? assetUrl(assets.back) : '';
  const previewItem = source
    ? { label: visible ? piece.label : 'Hidden component', source: assetUrl(source) }
    : undefined;
  usePreviewCandidate(piece.branch(), previewItem);
  const showActions = (x: number, y: number) => {
    const group = selectedPieceGroup(piece);
    const actions: ContextAction[] = [];
    const info = piece instanceof PlayPiece ? pieceInfoAction(piece, viewer) : undefined;
    const selectTop = topSelectionActions(zone, info, () => queueMicrotask(() => showActions(x, y)));
    if (selectTop) actions.push(selectTop);
    const visibilityAction = areaVisibilityAction(zone, viewer);
    if (visibilityAction) actions.push(visibilityAction);
    const stackOperations = stackOperationActions(zone, viewer);
    actions.push(...stackOperations);
    if (!selectTop && info) {
      actions.push({
        label: 'Info',
        icon: 'griddy-icons:info-circle',
        run: info.run,
      });
    }
    if (piece instanceof PlayPiece && !stackOperations.length && group.every(candidate => candidate instanceof PlayPiece && canChoose(candidate, 'flip'))) {
      actions.push({ label: group.length > 1 ? `Flip ${group.length}` : 'Flip', icon: 'mdi:flip-horizontal', run: () => flipSelection(piece) });
    }
    if (piece instanceof PlayDie && canChoose(piece, 'roll')) {
      actions.push({ label: 'Roll', icon: 'mdi:dice-6-outline', run: () => chooseElement(piece, 'roll') });
    }
    if (canChoose(piece, 'move')) {
      const targets = piece.game.moveTargetsForPieces(viewer, group);
      actions.push({
        label: group.length > 1 ? `Move ${group.length} to` : 'Move to',
        row: [{
          label: group.length > 1 ? `Move ${group.length} to` : 'Move to',
          icon: 'griddy-icons:arrow-right',
          run: () => beginChoice('move', {
            piece,
            ...(group.length > 1 ? { pieces: group } : {}),
          }),
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
    flip: piece instanceof PlayPiece && canChoose(piece, 'flip') ? () => flipSelection(piece) : undefined,
  };
  useHoverShortcuts(piece.branch(), hoverShortcuts);
  const { consumeClick, ...contextHandlers } = useContextTrigger(showActions, () => {
    if (selectable) setSelected([piece]);
  });
  return <div
    className={`play-piece piece-${piece instanceof PlayPiece ? piece.pieceType : 'd6'} ${piece instanceof PlayPiece && visible && piece.faceUp ? 'face-up' : 'face-down'}`}
    title={visible ? `${piece.label} · ${zoneSummary(zone)}` : zoneSummary(zone)}
    data-pnpink-branch={piece.branch()}
    data-pnpink-selectable={selectable ? 'true' : 'false'}
    data-pnpink-stack-selection={stackedSelection ? 'true' : undefined}
    data-pnpink-selection-anchor={selectionAnchor ? 'true' : undefined}
    style={selectionStyle}
    onPointerEnter={() => {
      setPreviewCandidate(previewItem);
    }}
    onPointerLeave={event => {
      const zoneNode = event.currentTarget.closest<HTMLElement>('.play-zone');
      const related = event.relatedTarget;
      if (!areaIsConcealedFrom(zone, viewer.position) && zoneNode && related instanceof Node && zoneNode.contains(related)) {
        setPreviewCandidate({ label: zoneSummary(zone), content: <AreaPreview area={zone} viewer={viewer} /> });
      } else {
        setPreviewCandidate();
      }
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
      if (selected.length > 1 && selected.some(candidate => candidate.branch() === piece.branch())) {
        event.preventDefault();
        event.stopPropagation();
        showActions(event.clientX, event.clientY);
      } else if (selected.length) {
        clearSelected();
      }
    }}
    {...contextHandlers}
  >
    {piece instanceof PlayPiece && ['card', 'tile', 'board', 'mat', 'area'].includes(piece.pieceType ?? 'card') ? <Flippable>
      <Flippable.Front>
        {front ? <img draggable={false} src={front} alt={piece.label} /> : <strong>{visible ? piece.label : '?'}</strong>}
      </Flippable.Front>
      <Flippable.Back>
        {back ? <img draggable={false} src={back} alt="Hidden component" /> : <strong>?</strong>}
      </Flippable.Back>
    </Flippable> : piece instanceof PlayPiece ? <span
      className={`resource-shape resource-${piece.pieceType} resource-${piece.shape}`}
      style={{ '--resource-color': piece.color || '#d8b64c' } as React.CSSProperties}
    >{piece.value}</span> : <span className="native-die-host" />}
    {selectionAnchor && <span className="selection-count">{selectedPieces.length}</span>}
  </div>;
}

function DieInteraction({ die }: { die: PlayDie }) {
  const selectable = canChoose(die, 'move');
  const showActions = (x: number, y: number) => {
    const dieActions: ContextAction[] = [];
    if (canChoose(die, 'roll')) {
      dieActions.push({ label: 'Roll', icon: 'mdi:dice-6-outline', run: () => chooseElement(die, 'roll') });
    }
    if (canChoose(die, 'setDie')) {
      dieActions.push(...[1, 2, 3, 4, 5, 6].map(value => ({
        label: String(value),
        run: () => submitChoice('setDie', { die, value }),
      })));
    }
    const actions: ContextAction[] = dieActions.length ? [{ label: 'Die actions', row: dieActions }] : [];
    if (actions.length) contextMenu.show({ title: '', x, y, actions });
  };
  const { consumeClick, ...contextHandlers } = useContextTrigger(showActions, () => {
    if (selectable) setSelected([die]);
  });
  return <span
    className="play-die-hitbox"
    data-pnpink-branch={die.branch()}
    data-pnpink-selectable={selectable ? 'true' : 'false'}
    onClickCapture={event => {
      if (!consumeClick()) return;
      event.preventDefault();
      event.stopPropagation();
    }}
    {...contextHandlers}
  />;
}

function toolboxBox(toolbox: PlayToolbox, viewer: PlayPlayer, portrait: boolean): Box {
  if (toolbox.scope === 'player' && toolbox.ownerPosition !== viewer.position) {
    return { left: -100, top: -100, width: 1, height: 1 };
  }
  if (portrait) {
    return toolbox.scope === 'shared'
      ? { left: 2, top: 1, width: 96, height: 9 }
      : { left: 2, top: 90, width: 96, height: 9 };
  }
  const areaCount = toolbox.game.areas().filter(candidate => (
    candidate.toolboxId === toolbox.definitionId && candidate.ownerPosition === toolbox.ownerPosition
  )).length;
  const height = Math.min(66, 8 + Math.max(1, areaCount) * 15);
  return toolbox.side === 'right'
    ? { left: 92.8, top: 99 - height, width: 6.5, height }
    : { left: .7, top: 99 - height, width: 6.5, height };
}

function toolboxAreaBox(zone: PlayArea, portrait: boolean): Box {
  const siblings = zone.game.areas().filter(candidate => (
    candidate.toolboxId === zone.toolboxId && candidate.ownerPosition === zone.ownerPosition
  ));
  const index = Math.max(0, siblings.indexOf(zone));
  if (portrait) {
    const width = 94 / Math.max(1, siblings.length);
    return { left: 3 + index * width, top: 18, width: width - 2, height: 76 };
  }
  const height = 88 / Math.max(1, siblings.length);
  return { left: 8, top: 9 + index * height, width: 84, height: height - 3 };
}

function boardBox(box: Box, portrait: boolean, withToolboxes: boolean): Box {
  if (!withToolboxes) return box;
  return portrait
    ? { left: box.left, top: 11 + box.top * .78, width: box.width, height: box.height * .78 }
    : { left: 7.5 + box.left * .85, top: box.top, width: box.width * .85, height: box.height };
}

function playfieldAspectRatio(): number {
  return manifest.board.aspectRatio * (manifest.toolboxes?.length ? .85 : 1);
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
    let origin: { x: number; y: number; pointerId: number } | undefined;
    let additive: GameElement[] = [];
    const activeTouches = new Set<number>();

    const update = (event: PointerEvent) => {
      if (!origin || event.pointerId !== origin.pointerId) return;
      if (activeTouches.size > 1) {
        origin = undefined;
        setRectangle(undefined);
        window.removeEventListener('pointermove', update);
        return;
      }
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

    const finish = (event?: PointerEvent) => {
      if (event?.pointerType === 'touch') activeTouches.delete(event.pointerId);
      if (event && origin && event.pointerId !== origin.pointerId) return;
      origin = undefined;
      setRectangle(undefined);
      window.removeEventListener('pointermove', update);
    };

    const start = (event: PointerEvent) => {
      if (event.pointerType === 'touch') {
        activeTouches.add(event.pointerId);
        if (activeTouches.size > 1) {
          finish();
          return;
        }
      }
      if (event.button !== 0 || (event.target as Element).closest('.Piece, .play-piece, .lil-gui, button, input, select')) return;
      origin = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
      additive = event.ctrlKey || event.metaKey ? selectedElements() : [];
      if (!additive.length) clearSelected();
      window.addEventListener('pointermove', update, { passive: false });
    };

    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        finish();
        clearSelected();
      }
    };

    window.addEventListener('pointerdown', start, true);
    window.addEventListener('pointerup', finish, true);
    window.addEventListener('pointercancel', finish, true);
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('pointerdown', start, true);
      window.removeEventListener('pointerup', finish, true);
      window.removeEventListener('pointercancel', finish, true);
      window.removeEventListener('keydown', keydown);
      finish();
    };
  }, []);

  return rectangle;
}

function relativePosition(zone: PlayArea, viewer: PlayPlayer): number {
  const count = zone.game.players.length;
  return (zone.ownerPosition - viewer.position + count) % count;
}

type PlayerSeat = {
  frame: Box;
  rotation: number;
  mirrored: boolean;
  placement: 'owner' | 'top' | 'left' | 'right';
};

function playerSeat(relative: number, count: number, portrait = false): PlayerSeat {
  const panelMode = manifest.players?.panelMode ?? 'rotate';
  if (relative === 0) {
    return { frame: { left: 1, top: 78, width: 98, height: 21 }, rotation: 0, mirrored: false, placement: 'owner' };
  }
  if (portrait) {
    const columns = Math.min(3, count - 1);
    const rows = Math.ceil((count - 1) / columns);
    const gap = 1;
    const width = (96 - gap * (columns - 1)) / columns;
    const height = Math.min(10, (24 - gap * (rows - 1)) / rows);
    const index = relative - 1;
    return {
      frame: {
        left: 2 + index % columns * (width + gap),
        top: 2 + Math.floor(index / columns) * (height + gap),
        width,
        height,
      },
      rotation: 0,
      mirrored: panelMode === 'rotate',
      placement: 'top',
    };
  }
  const opponents = Array.from({ length: count - 1 }, (_, index) => index + 1);
  let top: number[];
  let left: number[];
  let right: number[];
  if (opponents.length <= 2) {
    top = opponents;
    left = [];
    right = [];
  } else if (opponents.length <= 5) {
    left = opponents.slice(0, 1);
    right = opponents.slice(-1);
    top = opponents.slice(1, -1);
  } else {
    const topCount = Math.ceil(opponents.length / 3);
    const lateralCount = opponents.length - topCount;
    const leftCount = Math.floor(lateralCount / 2);
    left = opponents.slice(0, leftCount);
    top = opponents.slice(leftCount, leftCount + topCount);
    right = opponents.slice(leftCount + topCount);
  }

  const gap = 1;
  const topAvailable = 56;
  const sideAvailable = 48;
  const sideCount = Math.max(left.length, right.length);
  const topCapacity = top.length ? (topAvailable - gap * (top.length - 1)) / top.length : Number.POSITIVE_INFINITY;
  const aspectRatio = playfieldAspectRatio();
  const sideCapacity = sideCount
    ? (sideAvailable - gap * (sideCount - 1)) / sideCount / aspectRatio
    : Number.POSITIVE_INFINITY;
  const preferredWidth = count === 4 ? 27 : 24;
  const baseWidth = Math.min(preferredWidth, topCapacity / .8, sideCapacity / .8);
  const logicalWidth = baseWidth * .8;
  const logicalHeight = baseWidth / 1.35 * 1.2;

  if (top.includes(relative)) {
    const occupied = logicalWidth * top.length + gap * (top.length - 1);
    const frame = {
      left: 22 + (topAvailable - occupied) / 2 + top.indexOf(relative) * (logicalWidth + gap),
      top: 1.5,
      width: logicalWidth,
      height: logicalHeight,
    };
    return {
      frame,
      rotation: 0,
      mirrored: panelMode === 'rotate',
      placement: 'top',
    };
  }

  const onLeft = left.includes(relative);
  const side = onLeft ? left : right;
  const rotate = panelMode === 'rotate';
  const visualWidth = rotate ? logicalHeight / aspectRatio : logicalWidth;
  const visualHeight = rotate ? logicalWidth * aspectRatio : logicalHeight;
  const occupied = visualHeight * side.length + gap * (side.length - 1);
  const sideIndex = side.indexOf(relative);
  const visualIndex = onLeft ? side.length - sideIndex - 1 : sideIndex;
  const visualLeft = onLeft ? .8 : 99.2 - visualWidth;
  const visualTop = 26 + (sideAvailable - occupied) / 2 + visualIndex * (visualHeight + gap);
  const frame = {
    left: visualLeft + visualWidth / 2 - logicalWidth / 2,
    top: visualTop + visualHeight / 2 - logicalHeight / 2,
    width: logicalWidth,
    height: logicalHeight,
  };
  const rotation = rotate ? onLeft ? 90 : 270 : 0;
  return {
    frame,
    rotation,
    mirrored: false,
    placement: onLeft ? 'left' : 'right',
  };
}

function previewPieceSource(piece: PlayPiece, viewer: PlayPlayer): string {
  const source = visiblePieceSource(piece, viewer);
  return source ? assetUrl(source) : '';
}

function PreviewCards({ area, viewer, limit = 12 }: { area?: PlayArea; viewer: PlayPlayer; limit?: number }) {
  const pieces = area ? [...area.topN(limit, PlayPiece)] : [];
  return <>{pieces.map(piece => {
    const source = previewPieceSource(piece, viewer);
    return source
      ? <img key={piece.branch()} src={source} alt="" />
      : <span key={piece.branch()}>?</span>;
  })}</>;
}

function AreaPreview({ area, viewer }: { area: PlayArea; viewer: PlayPlayer }) {
  const kind = area.kind === 'hand' ? 'hand' : area.kind === 'tricks' ? 'tricks' : 'personal';
  return <div className={`area-preview area-preview-${kind}`}>
    <span className="area-preview-title">{zoneSummary(area)}</span>
    <span className="area-preview-icon iconify" data-icon={zoneIcon(area)} aria-hidden="true" />
    <div className="area-preview-cards"><PreviewCards area={area} viewer={viewer} /></div>
  </div>;
}

function PlayerPanelPreview({ player, viewer }: { player: PlayPlayer; viewer: PlayPlayer }) {
  const areas = player.game.areas().filter(area => area.ownerPosition === player.position);
  const hand = areas.find(area => area.kind === 'hand');
  const tricks = areas.find(area => area.kind === 'tricks');
  const personal = areas.find(area => area.kind === 'tableau');
  return <div className="player-panel-preview" style={{ '--player-color': player.color } as React.CSSProperties}>
    <strong>{player.name}</strong>
    <span className="player-panel-preview-icon iconify" data-icon="griddy-icons:user-circle" aria-hidden="true" />
    <div className="player-panel-preview-personal"><PreviewCards area={personal} viewer={viewer} limit={8} /></div>
    <div className="player-panel-preview-hand"><PreviewCards area={hand} viewer={viewer} /></div>
    <div className="player-panel-preview-tricks"><PreviewCards area={tricks} viewer={viewer} limit={8} /></div>
  </div>;
}

function PlayerPanelView({ panel, viewer, seat }: { panel: PlayPlayerPanel; viewer: PlayPlayer; seat: PlayerSeat }) {
  const player = panel.game.players.find(candidate => candidate.position === panel.ownerPosition)!;
  return <div
    className={`player-seat-panel ${seat.mirrored ? 'mirrored' : ''} ${seat.placement}-panel ${seat.placement === 'owner' ? '' : 'opponent-panel'} ${player.isCurrent() ? 'current-player' : ''}`}
    style={{
      '--player-color': player.color,
      '--player-tag-foreground': contrastingTextColor(player.color),
    } as React.CSSProperties}
    onPointerEnter={() => setPreviewCandidate({
      label: `${player.name}'s player area`,
      content: <PlayerPanelPreview player={player} viewer={viewer} />,
    })}
    onPointerLeave={() => setPreviewCandidate()}
  >
    <span className="player-seat-tag">
      <span className="player-seat-icon iconify" data-icon="griddy-icons:user-circle" aria-hidden="true" />
      <span className="player-seat-name">{player.name}</span>
    </span>
  </div>;
}

function rawZoneBox(zone: PlayArea, viewer: PlayPlayer): Box {
  const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId)!;
  if (definition.scope === 'shared') {
    return definition.layout.shared!;
  }
  return zone.ownerPosition === viewer.position ? definition.layout.owner! : definition.layout.opponent!;
}

function playerZoneBox(zone: PlayArea, viewer: PlayPlayer, boardSize: string): Box {
  const box = rawZoneBox(zone, viewer);
  if (zone.ownerPosition === viewer.position) return box;
  const seat = playerSeat(relativePosition(zone, viewer), zone.game.players.length, boardSize === 'portrait');
  return seat.mirrored ? { ...box, top: 100 - box.top - box.height } : box;
}

function zoneBox(zone: PlayArea, viewer: PlayPlayer, boardSize: string): Box {
  return boardBox(rawZoneBox(zone, viewer), boardSize === 'portrait', Boolean(manifest.toolboxes?.length));
}

function supplyLayout(definition: AreaDefinition, width: number, height: number) {
  return {
    area: { left: (100 - width) / 2, top: (100 - height) / 2, width, height },
    rows: definition.layout.rows ?? { min: 1 },
    columns: definition.layout.columns ?? { min: 1 },
    alignment: 'center' as const,
    scaling: 'fit' as const,
    gap: 0.1,
  };
}

function pieceLayout(definition: AreaDefinition, opponent = false) {
  if (definition.kind === 'hand') return {
    area: { left: opponent ? 13 : 2, top: opponent ? 17 : 9, width: opponent ? 85 : 96, height: opponent ? 80 : 88 },
    rows: 1,
    columns: { min: 1 },
    direction: 'ltr' as const,
    alignment: (opponent ? 'bottom' : 'center') as 'bottom' | 'center',
    scaling: 'fill' as const,
    maxOverlap: opponent ? 88 : 72,
    gap: opponent ? 0.08 : 0.25,
  };
  if (opponent && definition.kind === 'tricks') return {
    area: { left: 2, top: 17, width: 68, height: 80 },
    rows: 1,
    columns: 1,
    margin: 0,
  };
  if (definition.kind === 'tableau') return {
    area: opponent
      ? { left: 2, top: 3, width: 96, height: 94 }
      : { left: 3, top: 9, width: 94, height: 88 },
    rows: 2,
    columns: { min: 1 },
    direction: 'ltr' as const,
    alignment: 'center' as const,
    scaling: 'fit' as const,
    gap: definition.layout.gap ?? 0.25,
  };
  if (definition.kind === 'deck' || definition.kind === 'discard') return {
    area: { left: 3, top: 19, width: 94, height: 78 },
    rows: definition.layout.rows ?? { min: 1 },
    columns: definition.layout.columns ?? { min: 1 },
    margin: definition.layout.gap ?? 0.45,
  };
  return {
    area: { left: 3, top: 16, width: 94, height: 81 },
    rows: definition.layout.rows ?? { min: 1 },
    columns: definition.layout.columns ?? { min: 1 },
    margin: definition.layout.gap ?? 0.45,
  };
}

function trickSlot(piece: PlayPiece, viewer: PlayPlayer): Box {
  const count = piece.game.players.length;
  const relative = (piece.playedByPosition - viewer.position + count) % count;
  if (relative === 0) return { left: 40, top: 61, width: 20, height: 34 };
  if (relative === 1) return { left: 15, top: 31, width: 19, height: 32 };
  if (relative === count - 1) return { left: 66, top: 31, width: 19, height: 32 };
  return { left: 40, top: 5, width: 20, height: 34 };
}

function Table({ game, viewer, boardSize }: { game: PlayGame; viewer: PlayPlayer; boardSize: string }) {
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
  const hasToolboxes = Boolean(manifest.toolboxes?.length);
  return <main className={`play-table ${hasToolboxes ? 'has-toolboxes' : ''}`} style={{ '--felt-background': manifest.board.background, ...playerColors } as React.CSSProperties}>
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
    useD6(game);
    const hasToolboxes = Boolean(manifest.toolboxes?.length);
    game.appearance({ className: 'pnpink-play-root', render: current => <Table game={current} viewer={viewer} boardSize={boardSize} /> });
    game.all(PlayToolbox).appearance({ render: toolbox => <ToolboxView toolbox={toolbox} /> });
    game.all(PlayPlayerPanel).appearance({
      render: panel => <PlayerPanelView panel={panel} viewer={viewer} seat={playerSeat(
        (panel.ownerPosition - viewer.position + game.players.length) % game.players.length,
        game.players.length,
        boardSize === 'portrait',
      )} />,
    });
    game.all(PlayZone).appearance({ render: zone => <ZoneView zone={zone} viewer={viewer} /> });
    game.all(PlayStack).appearance({
      render: zone => <ZoneView zone={zone} viewer={viewer} />,
      effects: [{
        name: 'shuffle',
        trigger: (zone, previous) => zone.shuffleVersion !== previous.shuffleVersion,
      }],
    });
    game.all(PlayPiece).appearance({ aspectRatio: 0.7, render: piece => <PieceView piece={piece} viewer={viewer} /> });
    game.all(PlayResource).appearance({ aspectRatio: 1, render: piece => <PieceView piece={piece} viewer={viewer} /> });
    for (const die of game.all(PlayDie)) {
      const nativeRender = die._ui.appearance.render;
      die.appearance({
        className: 'D6 play-die',
        render: current => <>{nativeRender && nativeRender(current)}<DieInteraction die={current} /></>,
      });
    }
    for (const toolbox of game.toolboxes()) {
      game.layout(toolbox, { slots: [toolboxBox(toolbox, viewer, boardSize === 'portrait')] });
    }
    for (const panel of game.playerPanels()) {
      const seat = playerSeat((panel.ownerPosition - viewer.position + game.players.length) % game.players.length, game.players.length, boardSize === 'portrait');
      panel.rotation = seat.rotation;
      game.layout(panel, { slots: [boardBox(seat.frame, boardSize === 'portrait', hasToolboxes)] });
    }
    for (const zone of game.areas()) {
      const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId)!;
      if (zone.toolboxId) {
        const toolbox = game.toolboxes().find(candidate => (
          candidate.definitionId === zone.toolboxId && candidate.ownerPosition === zone.ownerPosition
        ));
        if (toolbox) toolbox.layout(zone, { slots: [toolboxAreaBox(zone, boardSize === 'portrait')] });
      } else if (zone.ownerPosition >= 0) {
        const panel = game.playerPanels().find(candidate => candidate.ownerPosition === zone.ownerPosition);
        if (panel) panel.layout(zone, { slots: [playerZoneBox(zone, viewer, boardSize)] });
      } else {
        game.layout(zone, { slots: [zoneBox(zone, viewer, boardSize)] });
      }
      if (zone.kind === 'center' || zone.kind === 'table') {
        for (const piece of areaItems(zone)) {
          if (piece instanceof PlayPiece && piece.playedByPosition >= 0) {
            zone.layout(piece, { slots: [trickSlot(piece, viewer)] });
          } else {
            const index = areaItems(zone).indexOf(piece);
            zone.layout(piece, { slots: [{ left: 8 + index % 7 * 12, top: 12 + Math.floor(index / 7) * 24, width: 10, height: 20 }] });
          }
        }
      } else if (zone instanceof PlayStack) {
        zone.configureLayout({ area: { left: 3, top: 16, width: 94, height: 81 } });
      } else if (zone.kind === 'supply') {
        zone.layout(PlayResource, supplyLayout(definition, 14, 16));
        zone.layout(PlayDie, supplyLayout(definition, 9.3, 10.7));
      } else {
        zone.layout(Piece, pieceLayout(definition, zone.kind === 'hand' && zone.ownerPosition !== viewer.position));
      }
    }
    game.layoutControls({ element: game, position: 'inset', left: 30, top: 44, width: 40, height: 12 });
  },
  infoModals: [{
    title: 'Generic table',
    modal: () => <div><h2>Manifest-driven play</h2><p>This table was built entirely from <code>play.json</code>. Draw, move, discard, flip and shuffle components, then end your turn.</p></div>,
  }],
});
