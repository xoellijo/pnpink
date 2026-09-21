const gameSelect = document.querySelector('#game');
const gameGrid = document.querySelector('#game-grid');
const roomsNode = document.querySelector('#rooms');
const searchNode = document.querySelector('#room-search');
const selectAllNode = document.querySelector('#select-all');
const deleteNode = document.querySelector('#delete-selected');
const selectedCountNode = document.querySelector('#selected-count');
const userSelect = document.querySelector('#active-user');
const userDialog = document.querySelector('#user-dialog');
const createButton = document.querySelector('#create-form button.primary-action');
const shareUserButton = document.querySelector('#share-user');
const installButton = document.querySelector('#install-app');

let roomData = [];
let installPrompt;
const selectedRooms = new Set();

async function loadGames() {
  const response = await fetch('/games', {cache: 'no-store'});
  if (!response.ok) throw new Error(await response.text());
  const data = await response.json();
  gameSelect.innerHTML = data.games.map(game =>
    `<option value="${PnPInkPlay.escapeHTML(game.name)}">${PnPInkPlay.escapeHTML(game.friendlyName)} · ${game.minPlayers}-${game.maxPlayers} players</option>`
  ).join('');
  gameGrid.innerHTML = data.games.map((game, index) =>
    `<div class="game-tile-shell"><button type="button" class="game-tile${index === 0 ? ' selected' : ''}" data-game="${PnPInkPlay.escapeHTML(game.name)}">
      <span class="game-cover"${game.cover ? ` style="background-image:url('${PnPInkPlay.escapeHTML(game.cover)}')"` : ''}><span>${PnPInkPlay.escapeHTML(game.friendlyName).slice(0, 1).toUpperCase()}</span></span>
      <span class="game-tile-copy"><strong>${PnPInkPlay.escapeHTML(game.friendlyName)}</strong><small>${game.minPlayers === game.maxPlayers ? game.minPlayers : `${game.minPlayers}–${game.maxPlayers}`} players</small></span>
    </button>${game.designer ? `<a class="game-designer-link" href="${PnPInkPlay.escapeHTML(game.designer)}" title="Open Game Designer">Design</a>` : ''}</div>`
  ).join('');
  gameGrid.querySelectorAll('.game-tile').forEach(tile => tile.addEventListener('click', () => {
    gameSelect.value = tile.dataset.game;
    gameGrid.querySelectorAll('.game-tile').forEach(candidate => candidate.classList.toggle('selected', candidate === tile));
  }));
}

function renderUsers() {
  const active = PnPInkPlay.activeUser();
  userSelect.innerHTML = PnPInkPlay.knownUsers().map(user =>
    `<option value="${PnPInkPlay.escapeHTML(user.token)}"${active?.id === user.id ? ' selected' : ''}>${PnPInkPlay.escapeHTML(user.name)}</option>`
  ).join('');
  createButton.disabled = !active;
  shareUserButton.disabled = !active;
}

async function importSharedUser() {
  const url = new URL(location.href);
  const token = url.searchParams.get('identity');
  if (!token) return;
  url.searchParams.delete('identity');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  const candidate = {token};
  const response = await PnPInkPlay.authFetch('/users/me', {}, candidate);
  if (!response.ok) throw new Error('This player identity link is no longer valid');
  const {user} = await response.json();
  PnPInkPlay.setActiveUser({...user, token});
}

function selectedVisibleRooms() {
  return [...document.querySelectorAll('.room-select:not([hidden]) input:checked')].map(input => Number(input.value));
}

function updateSelection() {
  const manageable = [...document.querySelectorAll('.room-select:not([hidden]) input')];
  const selected = selectedVisibleRooms();
  selectAllNode.checked = manageable.length > 0 && selected.length === manageable.length;
  selectAllNode.indeterminate = selected.length > 0 && selected.length < manageable.length;
  selectedCountNode.textContent = selectedRooms.size;
  deleteNode.disabled = selectedRooms.size === 0;
}

function activityText(room) {
  if (room.phase === 'new') return 'Waiting for players';
  if (room.phase === 'finished') return 'Finished';
  if (room.yourTurn) return 'Your turn';
  if (room.waitingFor?.length) return `Waiting for ${room.waitingFor.join(', ')}`;
  return 'In progress';
}

function renderRooms() {
  const query = searchNode.value.trim().toLowerCase();
  const filtered = roomData.filter(room =>
    !query || `${room.friendlyName} ${room.inviteCode} ${room.phase} ${activityText(room)}`.toLowerCase().includes(query)
  );
  if (!PnPInkPlay.activeUser()) {
    roomsNode.innerHTML = `<div class="empty-state"><span>${PnPInkPlay.icon('user')}</span><strong>Choose a player</strong><p>Each tab keeps its own active identity.</p></div>`;
    updateSelection();
    return;
  }
  if (!filtered.length) {
    roomsNode.innerHTML = `<div class="empty-state"><span>${PnPInkPlay.icon(roomData.length ? 'search' : 'sparks')}</span><strong>${roomData.length ? 'No matching games' : 'No games for this player'}</strong><p>${roomData.length ? 'Try another game name or room code.' : 'Create a room or open an invitation.'}</p></div>`;
    updateSelection();
    return;
  }
  roomsNode.innerHTML = filtered.map(room => {
    const checked = selectedRooms.has(room.id) ? ' checked' : '';
    const activityClass = room.yourTurn ? ' your-turn' : '';
    return `<article class="room-card${activityClass}">
      <label class="room-select" title="Select game"><input type="checkbox" value="${room.id}"${checked}><span></span></label>
      <div class="room-icon">${PnPInkPlay.escapeHTML(room.friendlyName).slice(0, 1).toUpperCase()}</div>
      <div class="room-info">
        <div class="room-title"><strong>${PnPInkPlay.escapeHTML(room.friendlyName)}</strong><span class="status ${PnPInkPlay.escapeHTML(room.phase)}">${PnPInkPlay.escapeHTML(room.phase)}</span></div>
        <div class="room-activity${activityClass}">${PnPInkPlay.escapeHTML(activityText(room))}</div>
        <div class="room-meta"><code>${PnPInkPlay.escapeHTML(room.inviteCode)}</code><span>${room.players}/${room.seatCount} players</span>${room.spectator ? '<span>Watching</span>' : ''}${room.owner ? '<span class="owner-tag">Admin</span>' : ''}</div>
      </div>
      <a class="room-action icon-action" href="/s/${room.token}" title="Resume game" aria-label="Resume game">${PnPInkPlay.icon('play')}</a>
    </article>`;
  }).join('');
  roomsNode.querySelectorAll('.room-select input').forEach(input => input.addEventListener('change', () => {
    const roomID = Number(input.value);
    if (input.checked) selectedRooms.add(roomID); else selectedRooms.delete(roomID);
    updateSelection();
  }));
  updateSelection();
}

async function loadRooms() {
  const active = PnPInkPlay.activeUser();
  selectedRooms.clear();
  if (!active) {
    roomData = [];
    renderRooms();
    return;
  }
  roomsNode.innerHTML = '<div class="loading"><span></span>Loading games…</div>';
  const response = await PnPInkPlay.authFetch('/users/me/rooms');
  if (!response.ok) throw new Error(await response.text());
  roomData = (await response.json()).rooms;
  renderRooms();
}

function openUserDialog() {
  document.querySelector('#user-error').textContent = '';
  document.querySelector('#new-user-name').value = '';
  if (userDialog.showModal) userDialog.showModal(); else userDialog.setAttribute('open', '');
  document.querySelector('#new-user-name').focus();
}

document.querySelector('#create-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await PnPInkPlay.authFetch('/sessions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({gameName: gameSelect.value, adminMode: document.querySelector('#admin-mode').checked}),
  });
  if (!response.ok) return alert(await response.text());
  const session = await response.json();
  PnPInkPlay.saveToken(session.sessionID, session.playerAccessToken);
  location.href = `/s/${session.playerAccessToken}`;
});

document.querySelector('#user-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.querySelector('#new-user-name').value}),
  });
  if (!response.ok) {
    document.querySelector('#user-error').textContent = await response.text();
    return;
  }
  const {user} = await response.json();
  PnPInkPlay.setActiveUser(user);
  userDialog.close?.();
  renderUsers();
  await loadRooms();
});

document.querySelector('#new-user').addEventListener('click', openUserDialog);
shareUserButton.addEventListener('click', async () => {
  const user = PnPInkPlay.activeUser();
  if (!user) return;
  const url = `${location.origin}/?identity=${encodeURIComponent(user.token)}`;
  try {
    await navigator.clipboard.writeText(url);
    alert('Player identity link copied. Treat it as a private link.');
  } catch (_error) {
    prompt('Copy this private player identity link:', url);
  }
});
document.querySelector('#cancel-user').addEventListener('click', () => userDialog.close?.());
userSelect.addEventListener('change', async () => {
  const user = PnPInkPlay.knownUsers().find(candidate => candidate.token === userSelect.value);
  if (user) PnPInkPlay.setActiveUser(user);
  renderUsers();
  await loadRooms();
});
searchNode.addEventListener('input', renderRooms);
selectAllNode.addEventListener('change', () => {
  document.querySelectorAll('.room-select:not([hidden]) input').forEach(input => {
    input.checked = selectAllNode.checked;
    const roomID = Number(input.value);
    if (input.checked) selectedRooms.add(roomID); else selectedRooms.delete(roomID);
  });
  updateSelection();
});
deleteNode.addEventListener('click', async () => {
  const selected = [...selectedRooms];
  if (!selected.length || !confirm(`Delete ${selected.length} selected game${selected.length === 1 ? '' : 's'}? This cannot be undone.`)) return;
  deleteNode.disabled = true;
  for (const roomID of selected) {
    const room = roomData.find(candidate => candidate.id === roomID);
    if (!room?.owner) continue;
    const response = await fetch(`/sessions/${room.token}`, {method: 'DELETE'});
    if (response.ok) PnPInkPlay.removeToken(roomID);
  }
  await loadRooms();
});
document.querySelector('#refresh').addEventListener('click', loadRooms);

installButton.addEventListener('click', async () => {
  if (installPrompt) {
    const prompt = installPrompt;
    installPrompt = undefined;
    await prompt.prompt();
    await prompt.userChoice;
    installButton.hidden = true;
  } else if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
    alert('Use Share, then Add to Home Screen.');
  }
});

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installPrompt = event;
  installButton.hidden = false;
});

window.addEventListener('appinstalled', () => {
  installPrompt = undefined;
  installButton.hidden = true;
});

if (/iPad|iPhone|iPod/.test(navigator.userAgent) && navigator.standalone !== true) installButton.hidden = false;

async function start() {
  await importSharedUser();
  renderUsers();
  await Promise.all([loadGames(), loadRooms()]);
  if (!PnPInkPlay.knownUsers().length) {
    document.querySelector('#cancel-user').hidden = true;
    userDialog.addEventListener('cancel', event => event.preventDefault());
    openUserDialog();
  }
}

start().catch(error => {
  renderUsers();
  roomsNode.innerHTML = `<p class="error">${PnPInkPlay.escapeHTML(error.message || error)}</p>`;
});
