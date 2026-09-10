import { createGame, Game, Piece, Player, Space } from '@boardzilla/core';
import { AreaAction, AreaDefinition, AreaVisibility, currentManifest, setupOperations } from '../manifest.js';

export const manifest = currentManifest();

export class PlayPlayer extends Player<PlayGame, PlayPlayer> {
  host = false;
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
}

export class PlayPiece extends Piece<PlayGame, PlayPlayer> {
  componentId = '';
  label = '';
  front = '';
  back = '';
  faceUp = true;
  playedByPosition = -1;
}

PlayPiece.revealWhenHidden('back', 'faceUp');

export function reorderActionName(zone: PlayZone): string {
  return `reorder-${zone.definitionId}`;
}

export class PlayGame extends Game<PlayGame, PlayPlayer> {
  repeatAction = false;
  trickLeaderPosition = 1;
  trickComplete = false;

  initialize(): void {
    for (const operation of setupOperations(manifest)) {
      if ('shuffle' in operation) {
        this.sharedZone(operation.shuffle)?.shuffle();
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
    for (const zone of this.all(PlayZone)) {
      for (const piece of zone.all(PlayPiece)) this.applyVisibility(piece, zone);
    }
  }

  sharedZone(id: string): PlayZone | undefined {
    return this.all(PlayZone).find(zone => zone.definitionId === id && zone.ownerPosition < 0);
  }

  playerZone(player: PlayPlayer, id: string): PlayZone | undefined {
    return this.all(PlayZone).find(zone => zone.definitionId === id && zone.ownerPosition === player.position);
  }

  zoneOf(piece: PlayPiece): PlayZone | undefined {
    return piece.container() instanceof PlayZone ? piece.container() as PlayZone : undefined;
  }

  ownerOf(zone: PlayZone): PlayPlayer | undefined {
    return zone.ownerPosition < 0
      ? undefined
      : this.players.find(player => player.position === zone.ownerPosition);
  }

  canControl(player: PlayPlayer, zone: PlayZone): boolean {
    return zone.ownerPosition < 0 || zone.ownerPosition === player.position;
  }

  zonesWith(actionName: AreaAction, player?: PlayPlayer): PlayZone[] {
    return this.all(PlayZone).filter(zone => (
      zone.actions.includes(actionName) && (!player || this.canControl(player, zone))
    ));
  }

  drawSources(): PlayZone[] {
    return this.zonesWith('draw').filter(zone => zone.all(PlayPiece).length > 0);
  }

  drawTargets(player: PlayPlayer): PlayZone[] {
    return this.zonesWith('move-to', player).filter(zone => zone.ownerPosition === player.position);
  }

  movablePieces(player: PlayPlayer): PlayPiece[] {
    return this.zonesWith('move-from', player).flatMap(zone => zone.all(PlayPiece));
  }

  moveTargets(player: PlayPlayer, piece: PlayPiece): PlayZone[] {
    const current = this.zoneOf(piece);
    return this.zonesWith('move-to', player).filter(zone => zone !== current);
  }

  moveTargetsForPieces(player: PlayPlayer, pieces: PlayPiece[]): PlayZone[] {
    return this.zonesWith('move-to', player).filter(zone => {
      const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId);
      return pieces.every(piece => this.zoneOf(piece) !== zone)
        && (definition?.maxDrop === undefined || pieces.length <= definition.maxDrop)
        && this.canPlayTrickPieces(player, pieces, zone);
    });
  }

  selectedPieces(player: PlayPlayer, anchor: PlayPiece, selected?: PlayPiece[]): PlayPiece[] {
    const movable = new Set(this.movablePieces(player));
    if (!selected) return movable.has(anchor) ? [anchor] : [];
    if (!selected.includes(anchor) || selected.some(piece => !(piece instanceof PlayPiece) || !movable.has(piece))) return [];
    const unique = [...new Set(selected)];
    if (unique.every(piece => piece.container() === unique[0].container())) {
      unique.sort((left, right) => left.position() - right.position());
    }
    return unique;
  }

  discardablePieces(player: PlayPlayer): PlayPiece[] {
    return this.movablePieces(player).filter(piece => this.zoneOf(piece)?.kind !== 'discard');
  }

  discardTargets(): PlayZone[] {
    return this.zonesWith('discard-to');
  }

  hand(player: PlayPlayer): PlayZone | undefined {
    return this.all(PlayZone).find(zone => zone.kind === 'hand' && zone.ownerPosition === player.position);
  }

  center(): PlayZone | undefined {
    return this.all(PlayZone).find(zone => zone.kind === 'center' || zone.kind === 'table');
  }

  trickPile(player: PlayPlayer): PlayZone | undefined {
    return this.all(PlayZone).find(zone => zone.definitionId === 'tricks' && zone.ownerPosition === player.position);
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

  canPlayTrickPieces(player: PlayPlayer, pieces: PlayPiece[], target: PlayZone): boolean {
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

  isTrickPlay(player: PlayPlayer, pieces: PlayPiece[], target: PlayZone): boolean {
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
    return this.movablePieces(player).filter(piece => Boolean(piece.back));
  }

  flippableStacks(player: PlayPlayer): PlayZone[] {
    return this.all(PlayZone).filter(zone => (
      this.canControl(player, zone)
      && zone.all(PlayPiece).length > 1
      && zone.all(PlayPiece).every(piece => Boolean(piece.back))
    ));
  }

  shuffleTargets(player: PlayPlayer): PlayZone[] {
    return this.all(PlayZone).filter(zone => (
      this.canControl(player, zone)
      && zone.all(PlayPiece).length > 1
      && zone.allowShuffle
    ));
  }

  dealSources(player: PlayPlayer): PlayZone[] {
    return this.all(PlayZone).filter(zone => (
      this.canControl(player, zone)
      && zone.allowDeal
      && this.maximumDealCount(zone) > 0
    ));
  }

  maximumDealCount(source: PlayZone): number {
    return Math.min(8, Math.floor(source.all(PlayPiece).length / this.players.length));
  }

  movePiece(piece: PlayPiece, target: PlayZone): void {
    piece.putInto(target, { position: target.insert === 'top' ? 0 : target.all(PlayPiece).length });
    this.applyVisibility(piece, target);
  }

  flipPiece(piece: PlayPiece): void {
    piece.faceUp = !piece.faceUp;
    const zone = this.zoneOf(piece);
    if (zone) this.applyVisibility(piece, zone);
  }

  applyVisibility(piece: PlayPiece, zone: PlayZone): void {
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
    if (piece.faceUp) piece.showToAll();
    else piece.hideFromAll();
  }
}

export default createGame(PlayPlayer, PlayGame, game => {
  const { action } = game;
  const { eachPlayer, forLoop, loop, playerActions } = game.flowCommands;
  game.registerClasses(PlayZone, PlayPiece);

  for (const definition of manifest.areas) {
    const pileByDefault = ['stack', 'deck', 'discard'].includes(definition.kind);
    const properties = {
      definitionId: definition.id,
      label: definition.label,
      kind: definition.kind,
      visibility: definition.visibility,
      actions: definition.actions ?? [],
      insert: definition.insert ?? 'top',
      allowShuffle: definition.allowShuffle ?? (definition.actions?.includes('shuffle') || pileByDefault),
      allowDeal: definition.allowDeal ?? (definition.actions?.includes('deal') || pileByDefault),
    };
    if (definition.scope === 'shared') {
      game.create(PlayZone, definition.id, { ...properties, ownerPosition: -1 });
    } else {
      for (const player of game.players) {
        game.create(PlayZone, definition.id, {
          ...properties,
          ownerPosition: player.position,
          player,
        });
      }
    }
  }

  for (const zone of game.all(PlayZone)) {
    zone.onEnter(PlayPiece, piece => game.applyVisibility(piece, zone));
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
        target.create(PlayPiece, `${definition.id}-${owner?.position ?? 'shared'}-${copy}`, {
          componentId: definition.id,
          label: definition.label,
          front: definition.front,
          back: definition.back ?? '',
          faceUp: definition.initial.faceUp ?? target.visibility === 'public',
        });
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
      const pieces: PlayPiece[] = zone ? [...zone.all(PlayPiece)] : [];
      const reorder = action({ prompt: `Reorder ${definition.label}`, condition: pieces.length > 1 })
        .chooseOnBoard('__reorder_from__', pieces, { prompt: `Choose a component in ${definition.label}` })
        .chooseOnBoard('__reorder_to__', rawArgs => {
          const { __reorder_from__, pieces: selected } = rawArgs as unknown as {
            __reorder_from__: PlayPiece;
            pieces?: PlayPiece[];
          };
          const group = game.selectedPieces(player, __reorder_from__ as PlayPiece, selected as PlayPiece[] | undefined);
          if (group.some(piece => game.zoneOf(piece) !== zone)) return [];
          return pieces.filter(piece => !group.includes(piece));
        }, { prompt: `Drag onto another component in ${definition.label}` });
      reorder.selections[0].clientContext = { dragInto: reorder.selections[1] };
      reorder.selections[1].clientContext = { dragFrom: reorder.selections[0] };
      return reorder
        .do(rawArgs => {
          const args = rawArgs as unknown as {
            __reorder_from__: PlayPiece;
            __reorder_to__: PlayPiece;
            pieces?: PlayPiece[];
          };
          const group = game.selectedPieces(player, args.__reorder_from__, args.pieces)
            .sort((left, right) => left.position() - right.position());
          const target = args.__reorder_to__;
          const parent = target.container() as PlayZone;
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
        const { piece, pieces } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[] };
        return game.moveTargetsForPieces(player, game.selectedPieces(player, piece, pieces));
      }, { prompt: 'Choose its destination' })
      .do(rawArgs => {
        const { piece, pieces, target } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[]; target: PlayZone };
        const selected = game.selectedPieces(player, piece, pieces);
        const trickPlay = game.isTrickPlay(player, selected, target);
        for (const selectedPiece of selected) {
          if (trickPlay) {
            selectedPiece.faceUp = true;
            selectedPiece.playedByPosition = player.position;
          } else if (target !== game.center()) {
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
        const { piece, pieces } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[] };
        const selected = game.selectedPieces(player, piece, pieces);
        const discardable = new Set(game.discardablePieces(player));
        if (!selected.length || selected.some(candidate => !discardable.has(candidate))) return [];
        return game.discardTargets().filter(target => {
          const definition = manifest.areas.find(candidate => candidate.id === target.definitionId);
          return definition?.maxDrop === undefined || selected.length <= definition.maxDrop;
        });
      }, { prompt: 'Choose a discard pile' })
      .do(rawArgs => {
        const { piece, pieces, target } = rawArgs as unknown as { piece: PlayPiece; pieces?: PlayPiece[]; target: PlayZone };
        for (const selectedPiece of game.selectedPieces(player, piece, pieces)) {
          selectedPiece.faceUp = true;
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
        target.shuffle();
        game.repeatAction = true;
      }).message('{{player}} shuffles {{target}}.'),

    deal: player => action({ prompt: 'Deal', condition: game.dealSources(player).length > 0 })
      .chooseOnBoard('source', game.dealSources(player), { prompt: 'Choose a deck or pile' })
      .chooseFrom('count', rawArgs => {
        const { source } = rawArgs as unknown as { source: PlayZone };
        return Array.from({ length: game.maximumDealCount(source) }, (_, index) => ({
          choice: index + 1,
          label: String(index + 1),
        }));
      }, { prompt: 'Cards per player' })
      .do(rawArgs => {
        const { source, count } = rawArgs as unknown as { source: PlayZone; count: number };
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
  const genericActions = ['draw', 'move', ...reorderActionNames, 'discard', 'flip', 'setFace', 'flipDeck', 'shuffle', 'deal', 'endTurn'];
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
              actions: ['move', ...reorderActionNames, 'flip', 'setFace', 'flipDeck', 'shuffle', 'deal'],
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
