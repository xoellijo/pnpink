import React from 'react';
import { ProfileBadge, render } from '@boardzilla/core';
import setup, { manifest, PlayGame, PlayPiece, PlayPlayer, PlayZone } from '../game/index.js';
import type { Box, ZoneDefinition } from '../manifest.js';
import './style.scss';

function ZoneView({ zone }: { zone: PlayZone }) {
  const owner = zone.ownerPosition >= 0
    ? zone.game.players.find(player => player.position === zone.ownerPosition)
    : undefined;
  return <div className={`play-zone zone-${zone.kind}`}><span>{zone.label}</span>{owner && <ProfileBadge player={owner} />}</div>;
}

function assetUrl(path: string): string {
  return path ? new URL(path, window.location.href.replace(/ui\.html$/, '')).href : '';
}

function PieceView({ piece, viewer }: { piece: PlayPiece; viewer: PlayPlayer }) {
  const visible = piece.isVisibleTo(viewer);
  const source = visible && piece.faceUp || !piece.back ? piece.front : piece.back;
  return <div className={`play-piece ${visible ? 'face-up' : 'face-down'}`} title={visible ? piece.label : 'Hidden component'}>
    {source ? <img src={assetUrl(source)} alt={visible ? piece.label : 'Hidden component'} /> : <strong>{visible ? piece.label : '?'}</strong>}
  </div>;
}

function zoneBox(zone: PlayZone, viewer: PlayPlayer): Box {
  const definition = manifest.zones.find(candidate => candidate.id === zone.definitionId)!;
  if (definition.scope === 'shared') return definition.layout.shared!;
  return zone.ownerPosition === viewer.position ? definition.layout.owner! : definition.layout.opponent!;
}

function pieceLayout(definition: ZoneDefinition) {
  return {
    rows: definition.layout.rows ?? { min: 1 },
    columns: definition.layout.columns ?? { min: 1 },
    margin: definition.layout.gap ?? 0.45,
  };
}

function Table({ game, viewer }: { game: PlayGame; viewer: PlayPlayer }) {
  return <main className="play-table" style={{ background: manifest.board.background }}>
    <header><div><h1>{manifest.name}</h1><p>{manifest.description}</p></div><ProfileBadge player={viewer} /></header>
    <footer>Click highlighted components and zones. Boardzilla keeps hands and face-down identities private.</footer>
  </main>;
}

render(setup, {
  boardSizes: [{ name: 'table', aspectRatio: manifest.board.aspectRatio, scaling: 'fit' }],
  layout: (game, viewer) => {
    game.appearance({ className: 'pnpink-play-root', render: current => <Table game={current} viewer={viewer} /> });
    game.all(PlayZone).appearance({ render: zone => <ZoneView zone={zone} /> });
    game.all(PlayPiece).appearance({ aspectRatio: 0.7, render: piece => <PieceView piece={piece} viewer={viewer} /> });
    for (const zone of game.all(PlayZone)) {
      const definition = manifest.zones.find(candidate => candidate.id === zone.definitionId)!;
      game.layout(zone, { slots: [zoneBox(zone, viewer)] });
      zone.layout(PlayPiece, pieceLayout(definition));
    }
    game.layoutControls({ element: game, position: 'inset', left: 30, top: 44, width: 40, height: 12 });
  },
  infoModals: [{
    title: 'Generic table',
    modal: () => <div><h2>Manifest-driven play</h2><p>This table was built entirely from <code>play.json</code>. Draw, move, discard, flip and shuffle components, then end your turn.</p></div>,
  }],
});
