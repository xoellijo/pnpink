import { createGame, Game, Piece, Player, Space } from '@boardzilla/core';
import rawManifest from '../../play.json';
import { PlayManifest, ZoneAction, ZoneDefinition, ZoneVisibility, validateManifest } from '../manifest.js';

export const manifest = validateManifest(rawManifest as PlayManifest);

export class PlayPlayer extends Player<PlayGame, PlayPlayer> {}

export class PlayZone extends Space<PlayGame, PlayPlayer> {
  definitionId = '';
  label = '';
  kind: ZoneDefinition['kind'] = 'table';
  ownerPosition = -1;
  visibility: ZoneVisibility = 'public';
  actions: ZoneAction[] = [];
}

export class PlayPiece extends Piece<PlayGame, PlayPlayer> {
  componentId = '';
  label = '';
  front = '';
  back = '';
  faceUp = true;
}

PlayPiece.revealWhenHidden('back');

export class PlayGame extends Game<PlayGame, PlayPlayer> {
  repeatAction = false;

  initialize(): void {
    for (const zoneId of manifest.setup?.shuffle ?? []) {
      this.sharedZone(zoneId)?.shuffle();
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

  zonesWith(actionName: ZoneAction, player?: PlayPlayer): PlayZone[] {
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

  discardablePieces(player: PlayPlayer): PlayPiece[] {
    return this.movablePieces(player).filter(piece => this.zoneOf(piece)?.kind !== 'discard');
  }

  discardTargets(): PlayZone[] {
    return this.zonesWith('discard-to');
  }

  flippablePieces(player: PlayPlayer): PlayPiece[] {
    return this.movablePieces(player).filter(piece => Boolean(piece.back));
  }

  shuffleTargets(player: PlayPlayer): PlayZone[] {
    return this.zonesWith('shuffle', player).filter(zone => zone.all(PlayPiece).length > 1);
  }

  movePiece(piece: PlayPiece, target: PlayZone): void {
    piece.putInto(target);
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

  for (const definition of manifest.zones) {
    if (definition.scope === 'shared') {
      game.create(PlayZone, definition.id, { ...definition, definitionId: definition.id, ownerPosition: -1 });
    } else {
      for (const player of game.players) {
        game.create(PlayZone, definition.id, {
          ...definition,
          definitionId: definition.id,
          ownerPosition: player.position,
          player,
        });
      }
    }
  }

  for (const definition of manifest.components) {
    const copies = definition.copies ?? 1;
    const owners = definition.initial.forEachPlayer ? game.players : [undefined];
    for (const owner of owners) {
      const target = owner
        ? game.playerZone(owner, definition.initial.zone)
        : game.sharedZone(definition.initial.zone);
      if (!target) throw new Error(`Initial zone not found: ${definition.initial.zone}`);
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

    move: player => action({ prompt: 'Move a component', condition: game.movablePieces(player).length > 0 })
      .chooseOnBoard('piece', game.movablePieces(player), { prompt: 'Choose a component' })
      .chooseOnBoard('target', ({ piece }) => game.moveTargets(player, piece), { prompt: 'Choose its destination' })
      .do(({ piece, target }) => {
        game.movePiece(piece, target);
        game.repeatAction = true;
      }).message('{{player}} moves {{piece}}.'),

    discard: player => action({ prompt: 'Discard a component', condition: game.discardablePieces(player).length > 0 && game.discardTargets().length > 0 })
      .chooseOnBoard('piece', game.discardablePieces(player), { prompt: 'Choose a component to discard' })
      .chooseOnBoard('target', game.discardTargets(), { prompt: 'Choose a discard pile' })
      .do(({ piece, target }) => {
        piece.faceUp = true;
        game.movePiece(piece, target);
        game.repeatAction = true;
      }).message('{{player}} discards {{piece}}.'),

    flip: player => action({ prompt: 'Flip a component', condition: game.flippablePieces(player).length > 0 })
      .chooseOnBoard('piece', game.flippablePieces(player), { prompt: 'Choose a component to flip' })
      .do(({ piece }) => {
        game.flipPiece(piece);
        game.repeatAction = true;
      }).message('{{player}} flips a component.'),

    shuffle: player => action({ prompt: 'Shuffle', condition: game.shuffleTargets(player).length > 0 })
      .chooseOnBoard('target', game.shuffleTargets(player), { prompt: 'Choose a deck, bag, or pile' })
      .do(({ target }) => {
        target.shuffle();
        game.repeatAction = true;
      }).message('{{player}} shuffles {{target}}.'),

    endTurn: () => action({ prompt: 'End turn' }).confirm('End your turn?'),
  });

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
          actions: ['draw', 'move', 'discard', 'flip', 'shuffle', 'endTurn'],
          skipIf: 'always',
        })],
      })],
    })),
  );
});
