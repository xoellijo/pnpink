const code = location.pathname.split('/').pop().toUpperCase();
const errorNode = document.querySelector('#error');
const usersNode = document.querySelector('#known-users');
const actionsNode = document.querySelector('#entry-actions');
const spectatorOnly = new URLSearchParams(location.search).has('spectate');
let selectedUser = null;
let room;

if (spectatorOnly) document.querySelector('#join-button').hidden = true;

function remember(session) {
  PnPInkPlay.setActiveUser(selectedUser);
  PnPInkPlay.saveToken(session.sessionID, session.token);
  location.replace(`/s/${session.token}`);
}

function selectUser(user) {
  selectedUser = PnPInkPlay.setActiveUser(user);
  renderUsers();
  document.querySelector('#selected-user').textContent = user.name;
  actionsNode.hidden = false;
}

function renderUsers() {
  const active = PnPInkPlay.activeUser();
  const participants = new Map((room?.players || []).map(player => [player.userID, player]));
  const users = PnPInkPlay.knownUsers();
  usersNode.innerHTML = users.length ? users.map(user => {
    const participant = participants.get(user.id);
    const status = participant ? participant.ready ? 'Ready' : 'Joined' : 'Available';
    return `<button type="button" class="identity-choice${active?.id === user.id ? ' active' : ''}" data-user="${PnPInkPlay.escapeHTML(user.id)}"><span>${PnPInkPlay.icon(participant?.ready ? 'check' : 'user')}</span><strong>${PnPInkPlay.escapeHTML(user.name)}</strong><small>${status}</small></button>`;
  }).join('') : '<p class="muted-copy">Create the first player for this device.</p>';
  usersNode.querySelectorAll('[data-user]').forEach(button => button.addEventListener('click', () => {
    selectUser(users.find(user => user.id === button.dataset.user));
  }));
}

async function enter(path, automatic = false) {
  if (!selectedUser) return;
  errorNode.textContent = '';
  const response = await PnPInkPlay.authFetch(path, {method: 'POST'}, selectedUser);
  if (response.ok) {
    remember(await response.json());
    return;
  }
  errorNode.textContent = await response.text();
  if (automatic) actionsNode.hidden = false;
}

async function start() {
  const response = await fetch(`/invites/${code}`);
  if (!response.ok) throw new Error(await response.text());
  room = await response.json();
  document.querySelector('#game-title').textContent = spectatorOnly ? `Watch ${room.friendlyName}` : `Join ${room.friendlyName}`;
  document.querySelector('#room-code').textContent = `Room ${code} · ${room.players.filter(player => !player.spectator).length}/${room.seatCount} players`;
  document.title = `PnPPlay - ${room.friendlyName}`;
  renderUsers();
  const active = PnPInkPlay.activeUser();
  if (active) {
    selectUser(active);
    document.querySelector('#game-title').textContent = `Opening ${room.friendlyName}…`;
    await enter(spectatorOnly ? `/invites/${code}/spectate` : `/invites/${code}`, true);
  } else {
    document.querySelector('#show-new-user').hidden = true;
    document.querySelector('#new-user-form').hidden = false;
    document.querySelector('#name').focus();
  }
}

document.querySelector('#show-new-user').addEventListener('click', () => {
  document.querySelector('#new-user-form').hidden = false;
  document.querySelector('#name').focus();
});

document.querySelector('#new-user-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.querySelector('#name').value}),
  });
  if (!response.ok) {
    errorNode.textContent = await response.text();
    return;
  }
  const {user} = await response.json();
  selectUser(user);
  document.querySelector('#new-user-form').hidden = true;
  await enter(spectatorOnly ? `/invites/${code}/spectate` : `/invites/${code}`);
});

document.querySelector('#join-button').addEventListener('click', () => enter(`/invites/${code}`));
document.querySelector('#spectate-button').addEventListener('click', () => enter(`/invites/${code}/spectate`));

start().catch(reason => { errorNode.textContent = reason.message || reason; });
