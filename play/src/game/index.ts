import { createGame, Game, Piece, Player, Space, Stack } from '@boardzilla/core';
import { D6 } from '@boardzilla/core/components';
import { AreaAction, AreaDefinition, AreaHideMode, AreaVisibility, currentManifest, PieceDefinition, setupOperations, ToolboxDefinition } from '../manifest.js';

export const manifest = currentManifest();

export class PlayPlayer extends Player<PlayGame, PlayPlayer> {
  host = false;
}

export class PlayToolbox extends Space<PlayGame, PlayPlayer> {
  definitionId = '';
  label = '';
  scope: ToolboxDefinition['scope'] = 'shared';
  side: ToolboxDefinition['side'] = 'left';
  ownerPosition = -1;
}

export class PlayZone extends Space<PlayGame, PlayPlayer> {
  definitionId = '';
  label = '';
  kind: AreaDefinition['kind'] = 'table';
  ownerPosition = -1;
  visibility: AreaVisibility = 'public';
  actions: AreaAction[] = [];
  insert: 'top' | 'bottom' = 'top';
  allowShuffle = false;
  allowDeal = false;
  canHide?: AreaHideMode;
  concealedFor: number[] = [];
  syncHidden = false;
  toolboxId = '';
  infinite = false;

  toJSON(seenBy?: number) {
    this.blockViewFor(seenBy !== undefined && areaIsConcealedFrom(this, seenBy) ? 'all' : 'none');
    return super.toJSON(seenBy);
  }
}

export class PlayStack extends Stack<PlayGame> {
  definitionId = '';
  label = '';
  kind: AreaDefinition['kind'] = 'stack';
  ownerPosition = -1;
  visibility: AreaVisibility = 'public';
  actions: AreaAction[] = [];
  insert: 'top' | 'bottom' = 'top';
  allowShuffle = true;
  allowDeal = true;
  shuffleVersion = 0;
  canHide?: AreaHideMode;
  concealedFor: number[] = [];
  syncHidden = false;
  toolboxId = '';
  infinite = false;

  toJSON(seenBy?: number) {
    this.blockViewFor(seenBy !== undefined && areaIsConcealedFrom(this, seenBy) ? 'all' : 'none');
    return super.toJSON(seenBy);
  }
}

export type PlayArea = PlayZone | PlayStack;

export function areaIsConcealedFrom(zone: PlayArea, position: number): boolean {
  if (!zone.canHide || zone.ownerPosition === position) return false;
  if (zone.canHide === 'sync') {
    return zone.game.areas().some(candidate => (
      candidate.definitionId === zone.definitionId && candidate.syncHidden
    ));
  }
  return zone.concealedFor.includes(position);
}

export function areaIsConcealed(zone: PlayArea): boolean {
  if (zone.canHide === 'sync') {
    return zone.game.areas().some(candidate => (
      candidate.definitionId === zone.definitionId && candidate.syncHidden
    ));
  }
  return zone.concealedFor.length > 0;
}

export class PlayPiece extends Piece<PlayGame, PlayPlayer> {
  componentId = '';
  label = '';
  front = '';
  back = '';
  faceUp = true;
  playedByPosition = -1;
  pieceType: PieceDefinition['type'] = 'card';
  color = '';
  value = '';
  homeAreaId = '';
  infiniteSource = false;
}

PlayPiece.revealWhenHidden('back', 'faceUp');

export class PlayResource extends PlayPiece {}

export class PlayDie extends D6<PlayGame> {
  componentId = '';
  label = '';
  color = '';
  value = '';
  homeAreaId = '';
  infiniteSource = false;
}

export type PlayItem = PlayPiece | PlayDie;
export type PlayDestination = PlayArea | PlayToolbox;

export function isPlayItem(value: unknown): value is PlayItem {
  return value instanceof PlayPiece || value instanceof PlayDie;
}

export function areaItems(zone: PlayArea): PlayItem[] {
  return [...zone.all(Piece)].filter(isPlayItem);
}

export function reorderActionName(zone: PlayArea): string {
  return `reorder-${zone.definitionId}`;
}

export class PlayGame extends Game<PlayGame, PlayPlayer> {
  repeatAction = false;
  trickLeaderPosition = 1;
  trickComplete = false;
  resourceSequence = 0;

  areas(): PlayArea[] {
    return [...this.all(PlayZone), ...this.all(PlayStack)];
  }

  toolboxes(): PlayToolbox[] {
    return this.all(PlayToolbox);
  }

  initialize(): void {
    for (const operation of setupOperations(manifest)) {
      if ('shuffle' in operation) {
        const target = this.sharedZone(operation.shuffle);
        if (target) this.shuffleArea(target);
      } else {
        const source = this.sharedZone(operation.deal.from);
        if (!source) continue;
        for (let card = 0; card < operation.deal.count; card += 1) {
          for (const player of this.players) {
            const target = this.playerZone(player, operation.deal.to);
            const piece = source.top(PlayPiece);
            if (!piece || !target) return;
            piece.faceUp = true;
            this.movePiece(piece, target);
          }
        }
      }
    }
    for (const zone of this.areas()) {
      for (const piece of areaItems(zone)) this.applyVisibility(piece, zone);
    }
  }

  sharedZone(id: string): PlayArea | undefined {
    return this.areas().find(zone => zone.definitionId === id && zone.ownerPosition < 0);
  }

  playerZone(player: PlayPlayer, id: string): PlayArea | undefined {
    return this.areas().find(zone => zone.definitionId === id && zone.ownerPosition === player.position);
  }

  zoneOf(piece: PlayItem): PlayArea | undefined {
    const container = piece.container();
    return container instanceof PlayZone || container instanceof PlayStack ? container : undefined;
  }

  ownerOf(zone: PlayArea): PlayPlayer | undefined {
    return zone.ownerPosition < 0
      ? undefined
      : this.players.find(player => player.position === zone.ownerPosition);
  }

  canControl(player: PlayPlayer, zone: PlayArea): boolean {
    return zone.ownerPosition < 0 || zone.ownerPosition === player.position;
  }

  zonesWith(actionName: AreaAction, player?: PlayPlayer): PlayArea[] {
    return this.areas().filter(zone => (
      zone.actions.includes(actionName) && (!player || this.canControl(player, zone))
    ));
  }

  drawSources(): PlayArea[] {
    return this.zonesWith('draw').filter(zone => zone.all(PlayPiece).length > 0);
  }

  drawTargets(player: PlayPlayer): PlayArea[] {
    return this.zonesWith('move-to', player).filter(zone => zone.ownerPosition === player.position);
  }

  movablePieces(player: PlayPlayer): PlayItem[] {
    return this.zonesWith('move-from', player).flatMap(areaItems);
  }

  moveTargetsForPieces(player: PlayPlayer, pieces: PlayItem[]): PlayDestination[] {
    const areas = this.zonesWith('move-to', player).filter(zone => {
      const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId);
      return pieces.every(piece => this.zoneOf(piece) !== zone)
        && (zone.kind !== 'supply' || pieces.every(piece => (
          piece.homeAreaId === zone.definitionId
          && (zone.ownerPosition < 0 || zone.ownerPosition === player.position)
        )))
        && (definition?.maxDrop === undefined || pieces.length <= definition.maxDrop)
        && this.canPlayTrickPieces(player, pieces.filter((piece): piece is PlayPiece => piece instanceof PlayPiece), zone);
    });
    const toolboxes = this.toolboxes().filter(toolbox => (
      (toolbox.ownerPosition < 0 || toolbox.ownerPosition === player.position)
      && pieces.every(piece => {
        const returnArea = this.returnAreaFor(piece, toolbox);
        return Boolean(returnArea && returnArea !== this.zoneOf(piece));
      })
    ));
    return [...areas, ...toolboxes];
  }

  selectedPieces(player: PlayPlayer, anchor: PlayItem, selected?: PlayItem[]): PlayItem[] {
    const movable = new Set(this.movablePieces(player));
    if (!selected) return movable.has(anchor) ? [anchor] : [];
    if (!selected.includes(anchor) || selected.some(piece => !isPlayItem(piece) || !movable.has(piece))) return [];
    const unique = [...new Set(selected)];
    if (unique.every(piece => piece.container() === unique[0].container())) {
      unique.sort((left, right) => left.position() - right.position());
    }
    return unique;
  }

  discardablePieces(player: PlayPlayer): PlayItem[] {
    return this.movablePieces(player).filter(piece => this.zoneOf(piece)?.kind !== 'discard');
  }

  returnAreaFor(piece: PlayItem, toolbox: PlayToolbox): PlayArea | undefined {
    if (!piece.homeAreaId) return undefined;
    return this.areas().find(zone => (
      zone.toolboxId === toolbox.definitionId
      && zone.definitionId === piece.homeAreaId
      && zone.ownerPosition === toolbox.ownerPosition
    ));
  }

  discardTargets(): PlayArea[] {
    return this.zonesWith('discard-to');
  }

  hand(player: PlayPlayer): PlayArea | undefined {
    return this.areas().find(zone => zone.kind === 'hand' && zone.ownerPosition === player.position);
  }

  center(): PlayArea | undefined {
    return this.areas().find(zone => zone.kind === 'center' || zone.kind === 'table');
  }

  trickPile(player: PlayPlayer): PlayArea | undefined {
    return this.areas().find(zone => zone.definitionId === 'tricks' && zone.ownerPosition === player.position);
  }

  playerAt(position: number): PlayPlayer {
    return this.players.find(player => player.position === position)!;
  }

  nextPlayer(player: PlayPlayer): PlayPlayer {
    const players = [...this.players];
    const index = players.findIndex(candidate => candidate.position === player.position);
    const reverse = manifest.turns?.order === 'left' || manifest.turns?.order === 'counterclockwise';
    const offset = reverse ? players.length - 1 : 1;
    return players[(index + offset) % players.length];
  }

  previousPlayer(player: PlayPlayer): PlayPlayer {
    const players = [...this.players];
    const index = players.findIndex(candidate => candidate.position === player.position);
    const reverse = manifest.turns?.order === 'left' || manifest.turns?.order === 'counterclockwise';
    const offset = reverse ? 1 : players.length - 1;
    return players[(index + offset) % players.length];
  }

  canPlayTrickPieces(player: PlayPlayer, pieces: PlayPiece[], target: PlayArea): boolean {
    if (manifest.turns?.mode !== 'trick-assisted' || target !== this.center()) return true;
    const maximum = manifest.turns.maxCardsPerPlay ?? 1;
    return !this.trickComplete
      && pieces.length > 0
      && (maximum === 0 || pieces.length <= maximum)
      && pieces.every(piece => {
        const source = this.zoneOf(piece);
        return source?.kind === 'hand' && source.ownerPosition === player.position;
      });
  }

  isTrickPlay(player: PlayPlayer, pieces: PlayPiece[], target: PlayArea): boolean {
    return manifest.turns?.mode === 'trick-assisted'
      && target === this.center()
      && this.canPlayTrickPieces(player, pieces, target);
  }

  trickDeciders(): PlayPlayer[] {
    switch (manifest.turns?.winnerDecider ?? 'last') {
      case 'all':
        return [...this.players];
      case 'host':
        return [this.players.find(player => player.host) ?? this.previousPlayer(this.playerAt(this.trickLeaderPosition))];
      case 'next-to-last':
        return [this.previousPlayer(this.previousPlayer(this.playerAt(this.trickLeaderPosition)))];
      default:
        return [this.previousPlayer(this.playerAt(this.trickLeaderPosition))];
    }
  }

  flippablePieces(player: PlayPlayer): PlayPiece[] {
    return this.movablePieces(player).filter((piece): piece is PlayPiece => piece instanceof PlayPiece && Boolean(piece.back));
  }

  flippableStacks(player: PlayPlayer): PlayArea[] {
    return this.areas().filter(zone => (
      this.canControl(player, zone)
      && areaItems(zone).length > 1
      && zone.all(PlayPiece).every(piece => Boolean(piece.back))
    ));
  }

  shuffleTargets(player: PlayPlayer): PlayArea[] {
    return this.areas().filter(zone => (
      this.canControl(player, zone)
      && areaItems(zone).length > 1
      && zone.allowShuffle
    ));
  }

  dealSources(player: PlayPlayer): PlayArea[] {
    return this.areas().filter(zone => (
      this.canControl(player, zone)
      && zone.allowDeal
      && this.maximumDealCount(zone) > 0
    ));
  }

  rollableDice(player: PlayPlayer): PlayDie[] {
    return this.movablePieces(player).filter((piece): piece is PlayDie => piece instanceof PlayDie);
  }

  hideableZones(player: PlayPlayer): PlayArea[] {
    return this.areas().filter(zone => zone.ownerPosition === player.position && Boolean(zone.canHide));
  }

  concealChoices(player: PlayPlayer, zone: PlayArea): Array<{ choice: string; label: string }> {
    if (zone.ownerPosition !== player.position || !zone.canHide) return [];
    if (zone.canHide === 'toggle') {
      const hidden = zone.concealedFor.length > 0;
      return [{ choice: hidden ? 'show' : 'hide', label: hidden ? 'Show area' : 'Hide area' }];
    }
    if (zone.canHide === 'sync') {
      return [{ choice: zone.syncHidden ? 'show' : 'hide', label: zone.syncHidden ? 'Show area' : 'Hide all matching areas' }];
    }
    const others = [...this.players].filter(candidate => candidate.position !== player.position);
    const allHidden = others.every(candidate => zone.concealedFor.includes(candidate.position));
    return [
      { choice: 'all', label: allHidden ? 'Show to everyone' : 'Hide from everyone' },
      ...others.map(candidate => ({
        choice: `player:${candidate.position}`,
        label: zone.concealedFor.includes(candidate.position) ? `Show to ${candidate.name}` : `Hide from ${candidate.name}`,
      })),
    ];
  }

  setAreaConcealment(player: PlayPlayer, zone: PlayArea, operation: string): void {
    if (zone.ownerPosition !== player.position || !zone.canHide) return;
    const others = [...this.players].filter(candidate => candidate.position !== player.position).map(candidate => candidate.position);
    if (zone.canHide === 'toggle') {
      zone.concealedFor = operation === 'hide' ? others : [];
      return;
    }
    if (zone.canHide === 'sync') {
      if (operation === 'hide') {
        for (const candidate of this.areas()) {
          if (candidate.definitionId === zone.definitionId) candidate.syncHidden = true;
        }
      } else if (operation === 'show') {
        zone.syncHidden = false;
      }
      return;
    }
    if (operation === 'all') {
      zone.concealedFor = others.every(position => zone.concealedFor.includes(position)) ? [] : others;
      return;
    }
    if (!operation.startsWith('player:')) return;
    const position = Number(operation.slice('player:'.length));
    if (!others.includes(position)) return;
    zone.concealedFor = zone.concealedFor.includes(position)
      ? zone.concealedFor.filter(candidate => candidate !== position)
      : [...zone.concealedFor, position];
  }

  maximumDealCount(source: PlayArea): number {
    return Math.min(8, Math.floor(source.all(PlayPiece).length / this.players.length));
  }

  shuffleArea(target: PlayArea): void {
    target.shuffle();
    if (target instanceof PlayStack) target.shuffleVersion += 1;
  }

  movePiece(piece: PlayItem, destination: PlayDestination): void {
    const source = this.zoneOf(piece);
    const target = destination instanceof PlayToolbox ? this.returnAreaFor(piece, destination) : destination;
    if (!target) return;
    const comesFromInfiniteSupply = piece.infiniteSource || Boolean(
      manifest.areas.find(candidate => candidate.id === piece.homeAreaId)?.infinite,
    );
    if (target.infinite && comesFromInfiniteSupply && piece.homeAreaId === target.definitionId) {
      piece.remove();
      return;
    }
    if (source?.infinite) {
      const replacement = piece.cloneInto(source) as PlayItem;
      this.applyVisibility(replacement, source);
    }
    if (target instanceof PlayStack && target.insert === 'top') piece.putInto(target);
    else piece.putInto(target, { position: target.insert === 'top' ? 0 : areaItems(target).length });
    this.applyVisibility(piece, target);
  }

  flipPiece(piece: PlayPiece): void {
    piece.faceUp = !piece.faceUp;
    const zone = this.zoneOf(piece);
    if (zone) this.applyVisibility(piece, zone);
  }

  applyVisibility(piece: PlayItem, zone: PlayArea): void {
    if (zone.visibility === 'hidden') {
      piece.hideFromAll();
      return;
    }
    if (zone.visibility === 'owner') {
      const owner = this.ownerOf(zone);
      if (owner) piece.showOnlyTo(owner);
      else piece.hideFromAll();
      return;
    }
    if (!(piece instanceof PlayPiece) || piece.faceUp) piece.showToAll();
    else piece.hideFromAll();
  }

  createItem(componentId: string, target: PlayArea, ownerPosition = -1): PlayItem {
    const definition = manifest.pieces.find(candidate => candidate.id === componentId);
    if (!definition) throw new Error(`Unknown component: ${componentId}`);
    const name = `${definition.id}-${ownerPosition < 0 ? 'shared' : ownerPosition}-${this.resourceSequence++}`;
    const common = {
      componentId: definition.id,
      label: definition.label,
      color: definition.color ?? '',
      value: String(definition.value ?? ''),
      homeAreaId: definition.initial.area,
      infiniteSource: target.infinite,
    };
    if (definition.type === 'd6') return target.create(PlayDie, name, common);
    const PieceClass = definition.type === 'token' || definition.type === 'chip' ? PlayResource : PlayPiece;
    return target.create(PieceClass, name, {
      ...common,
      pieceType: definition.type ?? 'card',
      front: definition.front ?? '',
      back: definition.back ?? '',
      faceUp: definition.initial.faceUp ?? target.visibility === 'public',
    });
  }
}

export default createGame(PlayPlayer, PlayGame, game => {
  const { action } = game;
  const { eachPlayer, forLoop, loop, playerActions } = game.flowCommands;
  game.registerClasses(PlayToolbox, PlayZone, PlayStack, PlayPiece, PlayResource, PlayDie);

  for (const definition of manifest.toolboxes ?? []) {
    const owners = definition.scope === 'player' ? game.players : [undefined];
    for (const owner of owners) {
      game.create(PlayToolbox, definition.id, {
        definitionId: definition.id,
        label: definition.label,
        scope: definition.scope,
        side: definition.side ?? (definition.scope === 'shared' ? 'left' : 'right'),
        ownerPosition: owner?.position ?? -1,
        player: owner,
      });
    }
  }

  for (const definition of manifest.areas) {
    const pileByDefault = ['stack', 'deck', 'discard', 'supply'].includes(definition.kind);
    const cardPileByDefault = ['stack', 'deck', 'discard'].includes(definition.kind);
    const properties = {
      definitionId: definition.id,
      label: definition.label,
      kind: definition.kind,
      visibility: definition.visibility,
      actions: definition.actions ?? [],
      insert: definition.insert ?? 'top',
      allowShuffle: definition.allowShuffle ?? (definition.actions?.includes('shuffle') || cardPileByDefault),
      allowDeal: definition.allowDeal ?? (definition.actions?.includes('deal') || cardPileByDefault),
      canHide: definition.canHide,
      toolboxId: definition.toolbox ?? '',
      infinite: definition.infinite ?? false,
    };
    if (definition.scope === 'shared') {
      const parent = definition.toolbox
        ? game.toolboxes().find(toolbox => toolbox.definitionId === definition.toolbox && toolbox.ownerPosition < 0) ?? game
        : game;
      if (pileByDefault) parent.create(PlayStack, definition.id, { ...properties, ownerPosition: -1 });
      else parent.create(PlayZone, definition.id, { ...properties, ownerPosition: -1 });
    } else {
      for (const player of game.players) {
        const playerProperties = { ...properties, ownerPosition: player.position, player };
        const parent = definition.toolbox
          ? game.toolboxes().find(toolbox => toolbox.definitionId === definition.toolbox && toolbox.ownerPosition === player.position) ?? game
          : game;
        if (pileByDefault) parent.create(PlayStack, definition.id, playerProperties);
        else parent.create(PlayZone, definition.id, playerProperties);
      }
    }
  }

  for (const zone of game.areas()) {
    if (zone.visibility === 'public') zone.contentsWillBeShown();
    else if (zone.visibility === 'owner') zone.contentsWillBeShownToOwner();
    else zone.contentsWillBeHidden();
    zone.onEnter(PlayPiece, piece => game.applyVisibility(piece, zone));
    zone.onEnter(PlayDie, piece => game.applyVisibility(piece, zone));
  }

  for (const definition of manifest.pieces) {
    const copies = definition.copies ?? 1;
    const owners = definition.initial.forEachPlayer ? game.players : [undefined];
    for (const owner of owners) {
      const target = owner
        ? game.playerZone(owner, definition.initial.area)
        : game.sharedZone(definition.initial.area);
      if (!target) throw new Error(`Initial area not found: ${definition.initial.area}`);
      for (let copy = 0; copy < copies; copy += 1) {
        game.createItem(definition.id, target, owner?.position ?? -1);
      }
    }
  }

  const reorderDefinitions = manifest.areas.filter(definition => definition.actions?.includes('move-from'));
  const reorderActions = Object.fromEntries(reorderDefinitions.map(definition => [
    `reorder-${definition.id}`,
    (player: PlayPlayer) => {
      const zone = definition.scope === 'shared'
        ? game.sharedZone(definition.id)
        : game.playerZone(player, definition.id);
      const pieces: PlayItem[] = zone ? areaItems(zone) : [];
      const reorder = action({ prompt: `Reorder ${definition.label}`, condition: pieces.length > 1 })
        .chooseOnBoard('__reorder_from__', pieces, { prompt: `Choose a component in ${definition.label}` })
        .chooseOnBoard('__reorder_to__', rawArgs => {
          const { __reorder_from__, pieces: selected } = rawArgs as unknown as {
            __reorder_from__: PlayItem;
            pieces?: PlayItem[];
          };
          const group = game.selectedPieces(player, __reorder_from__, selected);
          if (group.some(piece => game.zoneOf(piece) !== zone)) return [];
          return pieces.filter(piece => !group.includes(piece));
        }, { prompt: `Drag onto another component in ${definition.label}` });
      reorder.selections[0].clientContext = { dragInto: reorder.selections[1] };
      reorder.selections[1].clientContext = { dragFrom: reorder.selections[0] };
      return reorder
        .do(rawArgs => {
          const args = rawArgs as unknown as {
            __reorder_from__: PlayItem;
            __reorder_to__: PlayItem;
            pieces?: PlayItem[];
          };
          const group = game.selectedPieces(player, args.__reorder_from__, args.pieces)
            .sort((left, right) => left.position() - right.position());
          const target = args.__reorder_to__;
          const parent = target.container() as PlayArea;
          for (const piece of group) piece.putInto(parent);
          const position = target.position();
          for (const piece of [...group].reverse()) piece.putInto(parent, { position });
          game.repeatAction = true;
        })
        .message(`{{player}} reorders ${definition.label}.`);
    },
  ]));

  const moveAction = (player: PlayPlayer) => {
    const result = action({ prompt: 'Move a component', condition: game.movablePieces(player).length > 0 })
      .chooseOnBoard('piece', game.movablePieces(player), { prompt: 'Choose a component' })
      .chooseOnBoard('target', rawArgs => {
        const { piece, pieces } = rawArgs as unknown as { piece: PlayItem; pieces?: PlayItem[] };
        return game.moveTargetsForPieces(player, game.selectedPieces(player, piece, pieces));
      }, { prompt: 'Choose its destination' })
      .do(rawArgs => {
        const { piece, pieces, target } = rawArgs as unknown as { piece: PlayItem; pieces?: PlayItem[]; target: PlayDestination };
        const selected = game.selectedPieces(player, piece, pieces);
        const selectedCards = selected.filter((candidate): candidate is PlayPiece => candidate instanceof PlayPiece);
        const trickPlay = target instanceof PlayToolbox ? false : selectedCards.length === selected.length && game.isTrickPlay(player, selectedCards, target);
        for (const selectedPiece of selected) {
          if (trickPlay && selectedPiece instanceof PlayPiece) {
            selectedPiece.faceUp = true;
            selectedPiece.playedByPosition = player.position;
          } else if (selectedPiece instanceof PlayPiece && target !== game.center()) {
            selectedPiece.playedByPosition = -1;
          }
          game.movePiece(selectedPiece, target);
        }
        game.repeatAction = !trickPlay;
      }).message('{{player}} moves {{piece}}.');
    result.selections[0].clientContext = { dragInto: result.selections[1] };
    result.selections[1].clientContext = { dragFrom: result.selections[0] };
    return result;
  };

  const discardAction = (player: PlayPlayer) => {
    const result = action({ prompt: 'Discard a component', condition: game.discardablePieces(player).length > 0 && game.discardTargets().length > 0 })
      .chooseOnBoard('piece', game.discardablePieces(player), { prompt: 'Choose a component to discard' })
      .chooseOnBoard('target', rawArgs => {
        const { piece, pieces } = rawArgs as unknown as { piece: PlayItem; pieces?: PlayItem[] };
        const selected = game.selectedPieces(player, piece, pieces);
        const discardable = new Set(game.discardablePieces(player));
        if (!selected.length || selected.some(candidate => !discardable.has(candidate))) return [];
        return game.discardTargets().filter(target => {
          const definition = manifest.areas.find(candidate => candidate.id === target.definitionId);
          return definition?.maxDrop === undefined || selected.length <= definition.maxDrop;
        });
      }, { prompt: 'Choose a discard pile' })
      .do(rawArgs => {
        const { piece, pieces, target } = rawArgs as unknown as { piece: PlayItem; pieces?: PlayItem[]; target: PlayArea };
        for (const selectedPiece of game.selectedPieces(player, piece, pieces)) {
          if (selectedPiece instanceof PlayPiece) selectedPiece.faceUp = true;
          game.movePiece(selectedPiece, target);
        }
        game.repeatAction = true;
      }).message('{{player}} discards {{piece}}.');
    result.selections[0].clientContext = { dragInto: result.selections[1] };
    result.selections[1].clientContext = { dragFrom: result.selections[0] };
    return result;
  };

  game.defineActions({
    draw: player => action({ prompt: 'Draw a component', condition: game.drawSources().length > 0 && game.drawTargets(player).length > 0 })
      .chooseOnBoard('source', game.drawSources(), { prompt: 'Choose a deck or bag' })
      .chooseOnBoard('target', game.drawTargets(player), { prompt: 'Choose one of your zones' })
      .do(({ source, target }) => {
        const piece = source.top(PlayPiece)!;
        piece.faceUp = true;
        game.movePiece(piece, target);
        game.repeatAction = true;
      }).message('{{player}} draws a component.'),

    move: moveAction,

    discard: discardAction,

    flip: player => action({ prompt: 'Flip a component', condition: game.flippablePieces(player).length > 0 })
      .chooseOnBoard('piece', game.flippablePieces(player), { prompt: 'Choose a component to flip' })
      .do(rawArgs => {
        const { piece, pieces } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[] };
        const flippable = new Set(game.flippablePieces(player));
        const selected = pieces?.length ? [...new Set(pieces)] : [piece];
        if (!selected.includes(piece) || selected.some(candidate => !flippable.has(candidate))) return;
        for (const selectedPiece of selected) game.flipPiece(selectedPiece);
        game.repeatAction = true;
      }).message('{{player}} flips a component.'),

    setFace: player => action({ prompt: 'Set component face', condition: game.flippablePieces(player).length > 0 })
      .chooseOnBoard('piece', game.flippablePieces(player), { prompt: 'Choose a component' })
      .chooseFrom('faceUp', [
        { choice: true, label: 'Face up' },
        { choice: false, label: 'Face down' },
      ], { prompt: 'Choose orientation' })
      .do(rawArgs => {
        const { piece, pieces, faceUp } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[]; faceUp: boolean };
        const flippable = new Set(game.flippablePieces(player));
        const selected = pieces?.length ? [...new Set(pieces)] : [piece];
        if (!selected.includes(piece) || selected.some(candidate => !flippable.has(candidate))) return;
        for (const selectedPiece of selected) {
          if (selectedPiece.faceUp !== faceUp) game.flipPiece(selectedPiece);
        }
        game.repeatAction = true;
      }).message('{{player}} sets component faces.'),

    flipDeck: player => action({ prompt: 'Flip deck', condition: game.flippableStacks(player).length > 0 })
      .chooseOnBoard('target', game.flippableStacks(player), { prompt: 'Choose a deck or pile' })
      .do(({ target }) => {
        for (const piece of [...target.all(PlayPiece)]) {
          game.flipPiece(piece);
          piece.putInto(target, { position: 0 });
        }
        game.repeatAction = true;
      }).message('{{player}} flips {{target}}.'),

    shuffle: player => action({ prompt: 'Shuffle', condition: game.shuffleTargets(player).length > 0 })
      .chooseOnBoard('target', game.shuffleTargets(player), { prompt: 'Choose a deck, bag, or pile' })
      .do(({ target }) => {
        game.shuffleArea(target);
        game.repeatAction = true;
      }).message('{{player}} shuffles {{target}}.'),

    deal: player => action({ prompt: 'Deal', condition: game.dealSources(player).length > 0 })
      .chooseOnBoard('source', game.dealSources(player), { prompt: 'Choose a deck or pile' })
      .chooseFrom('count', rawArgs => {
        const { source } = rawArgs as unknown as { source: PlayArea };
        return Array.from({ length: game.maximumDealCount(source) }, (_, index) => ({
          choice: index + 1,
          label: String(index + 1),
        }));
      }, { prompt: 'Cards per player' })
      .do(rawArgs => {
        const { source, count } = rawArgs as unknown as { source: PlayArea; count: number };
        for (let round = 0; round < count; round += 1) {
          for (const targetPlayer of game.players) {
            const target = game.hand(targetPlayer);
            const piece = source.top(PlayPiece);
            if (!piece || !target) return;
            piece.faceUp = true;
            game.movePiece(piece, target);
          }
        }
        game.repeatAction = true;
      }).message('{{player}} deals cards.'),

    roll: player => action({ prompt: 'Roll a die', condition: game.rollableDice(player).length > 0 })
      .chooseOnBoard('die', game.rollableDice(player), { prompt: 'Choose a die' })
      .do(({ die }) => {
        die.roll();
        game.repeatAction = true;
      }).message('{{player}} rolls {{die}}.'),

    concealArea: player => action({ condition: game.hideableZones(player).length > 0 })
      .chooseOnBoard('zone', game.hideableZones(player), { skipIf: 'never' })
      .chooseFrom('operation', ({ zone }) => game.concealChoices(player, zone), { prompt: 'Choose visibility' })
      .do(({ zone, operation }) => {
        game.setAreaConcealment(player, zone, operation);
        game.repeatAction = true;
      }).message('{{player}} changes an area visibility.'),

    takeTrick: () => action({ prompt: 'Take trick', condition: game.trickComplete })
      .chooseFrom('winner', [...game.players].map(player => ({
        choice: player.position,
        label: player.name,
      })), { prompt: 'Who won this trick?' })
      .do(({ winner }) => {
        const player = game.playerAt(winner);
        const center = game.center();
        const target = game.trickPile(player);
        if (!center || !target) return;
        for (const card of center.all(PlayPiece)) {
          card.playedByPosition = -1;
          game.movePiece(card, target);
        }
        game.trickLeaderPosition = winner;
        game.trickComplete = false;
      }).message('{{winner}} takes the trick.', ({ winner }) => ({
        winner: game.playerAt(winner),
      })),

    endTurn: () => action({ prompt: 'End turn' }).confirm('End your turn?'),
    ...reorderActions,
  });

  const reorderActionNames = reorderDefinitions.map(definition => `reorder-${definition.id}`);
  const genericActions = ['draw', 'move', ...reorderActionNames, 'discard', 'flip', 'setFace', 'flipDeck', 'shuffle', 'deal', 'roll', 'concealArea', 'endTurn'];
  const turnMode = manifest.turns?.mode ?? 'guided';
  const maximumTrickCards = manifest.turns?.maxCardsPerPlay ?? 1;
  if (turnMode === 'free') {
    game.defineFlow(
      () => game.initialize(),
      loop(playerActions({
        name: 'freeAction',
        players: () => [...game.players],
        prompt: 'Choose a tabletop action',
        actions: genericActions,
      })),
    );
  } else if (turnMode === 'trick-assisted') {
    game.defineFlow(
      () => game.initialize(),
      loop(
        eachPlayer({
          name: 'trickPlayer',
          startingPlayer: () => game.playerAt(game.trickLeaderPosition),
          nextPlayer: player => game.nextPlayer(player),
          do: [forLoop({
            name: 'trickAction',
            initial: 1,
            next: step => game.repeatAction ? (game.repeatAction = false, step) : step + 1,
            while: step => step <= 1,
            do: [playerActions({
              name: 'playTrickCard',
              prompt: maximumTrickCards === 0
                ? 'Play one or more cards'
                : `Play up to ${maximumTrickCards} card${maximumTrickCards === 1 ? '' : 's'}`,
              actions: ['move', ...reorderActionNames, 'flip', 'setFace', 'flipDeck', 'shuffle', 'deal', 'roll', 'concealArea'],
              skipIf: 'always',
            })],
          })],
        }),
        () => { game.trickComplete = true; },
        playerActions({
          name: 'resolveTrick',
          players: () => game.trickDeciders(),
          prompt: 'Trick complete',
          actions: ['takeTrick'],
          skipIf: 'always',
        }),
      ),
    );
  } else {
    game.defineFlow(
      () => game.initialize(),
      loop(eachPlayer({
        name: 'turnPlayer',
        do: [forLoop({
          name: 'tabletopAction',
          initial: 1,
          next: step => game.repeatAction ? (game.repeatAction = false, step) : step + 1,
          while: step => step <= 1,
          do: [playerActions({
            name: 'genericAction',
            prompt: 'Choose a tabletop action',
            actions: genericActions,
            skipIf: 'always',
          })],
        })],
      })),
    );
  }
});
