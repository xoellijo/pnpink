const gameSelect = document.querySelector('#game');
const roomsNode = document.querySelector('#rooms');
const searchNode = document.querySelector('#room-search');
const selectAllNode = document.querySelector('#select-all');
const deleteNode = document.querySelector('#delete-selected');
const selectedCountNode = document.querySelector('#selected-count');

let roomData = [];
let accessByRoom = new Map();
const selectedRooms = new Set();

async function loadGames() {
  const data = await fetch('/games').then(response => response.json());
  gameSelect.innerHTML = data.games.map(game =>
    `<option value="${PnPInkPlay.escapeHTML(game.name)}">${PnPInkPlay.escapeHTML(game.friendlyName)} Â· ${game.minPlayers}-${game.maxPlayers} players</option>`
  ).join('');
}

async function resolveAccess(rooms) {
  const tokens = PnPInkPlay.loadTokens();
  const entries = await Promise.all(rooms.map(async room => {
    const token = tokens[room.id];
    if (!token) return [room.id, null];
    try {
      const response = await fetch(`/sessions/${token}`);
      if (!response.ok) throw new Error();
      const session = await response.json();
      return [room.id, {token, owner: session.host, administrator: session.administrator}];
    } catch (_error) {
      PnPInkPlay.removeToken(room.id);
      return [room.id, null];
    }
  }));
  accessByRoom = new Map(entries);
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

function roomAction(room, access) {
  if (access) return `<a class="room-action icon-action" href="/s/${access.token}" title="Resume game" aria-label="Resume game">${PnPInkPlay.icon('play')}</a>`;
  if (room.phase === 'new') return `<a class="room-action secondary icon-action" href="/join/${room.inviteCode}" title="Join as player" aria-label="Join as player">${PnPInkPlay.icon('user-plus')}</a>`;
  return `<a class="room-action secondary icon-action" href="/join/${room.inviteCode}?spectate=1" title="Watch as spectator" aria-label="Watch as spectator">${PnPInkPlay.icon('eye')}</a>`;
}

function renderRooms() {
  const query = searchNode.value.trim().toLowerCase();
  const filtered = roomData.filter(room =>
    !query || `${room.friendlyName} ${room.inviteCode} ${room.phase}`.toLowerCase().includes(query)
  );
  if (!filtered.length) {
    roomsNode.innerHTML = `<div class="empty-state"><span>${PnPInkPlay.icon(roomData.length ? 'search' : 'sparks')}</span><strong>${roomData.length ? 'No matching rooms' : 'No rooms yet'}</strong><p>${roomData.length ? 'Try another game name or room code.' : 'Create your first persistent playtest.'}</p></div>`;
    updateSelection();
    return;
  }
  roomsNode.innerHTML = filtered.map(room => {
    const access = accessByRoom.get(room.id);
    const owner = Boolean(access?.owner);
    const checked = selectedRooms.has(room.id) ? ' checked' : '';
    return `<article class="room-card">
      <label class="room-select"${owner ? '' : ' hidden'} title="Select room"><input type="checkbox" value="${room.id}"${checked}><span></span></label>
      <div class="room-icon">${PnPInkPlay.escapeHTML(room.friendlyName).slice(0, 1).toUpperCase()}</div>
      <div class="room-info">
        <div class="room-title"><strong>${PnPInkPlay.escapeHTML(room.friendlyName)}</strong><span class="status ${PnPInkPlay.escapeHTML(room.phase)}">${PnPInkPlay.escapeHTML(room.phase)}</span></div>
        <div class="room-meta"><code>${PnPInkPlay.escapeHTML(room.inviteCode)}</code><span>${room.players}/${room.seatCount} players</span><span>${room.spectators || 0} watching</span>${owner ? '<span class="owner-tag">Yours</span>' : ''}</div>
      </div>
      ${roomAction(room, access)}
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
  roomsNode.innerHTML = '<div class="loading"><span></span>Loading roomsâ€¦</div>';
  const data = await fetch('/rooms').then(response => response.json());
  roomData = data.rooms;
  await resolveAccess(roomData);
  for (const roomID of [...selectedRooms]) {
    if (!accessByRoom.get(roomID)?.owner) selectedRooms.delete(roomID);
  }
  renderRooms();
}

document.querySelector('#create-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/sessions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      gameName: gameSelect.value,
      name: document.querySelector('#name').value,
      adminMode: document.querySelector('#admin-mode').checked,
    }),
  });
  if (!response.ok) return alert(await response.text());
  const session = await response.json();
  PnPInkPlay.saveToken(session.sessionID, session.playerAccessToken);
  location.href = `/s/${session.playerAccessToken}`;
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
  if (!selected.length || !confirm(`Delete ${selected.length} selected room${selected.length === 1 ? '' : 's'}? This cannot be undone.`)) return;
  deleteNode.disabled = true;
  for (const roomID of selected) {
    const access = accessByRoom.get(roomID);
    if (!access?.owner) continue;
    const response = await fetch(`/sessions/${access.token}`, {method: 'DELETE'});
    if (response.ok) {
      PnPInkPlay.removeToken(roomID);
      selectedRooms.delete(roomID);
    }
  }
  await loadRooms();
});
document.querySelector('#refresh').addEventListener('click', loadRooms);

Promise.all([loadGames(), loadRooms()]).catch(error => {
  roomsNode.innerHTML = `<p class="error">${PnPInkPlay.escapeHTML(error)}</p>`;
});

