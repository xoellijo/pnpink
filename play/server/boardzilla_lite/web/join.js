const code = location.pathname.split('/').pop().toUpperCase();
const errorNode = document.querySelector('#error');
const spectatorOnly = new URLSearchParams(location.search).has('spectate');
if (spectatorOnly) document.querySelector('#join-button').hidden = true;

function remember(session) {
  PnPInkPlay.saveToken(session.sessionID, session.token);
  location.href = `/s/${session.token}`;
}

fetch(`/invites/${code}`).then(async response => {
  if (!response.ok) throw new Error(await response.text());
  const room = await response.json();
  document.querySelector('#game-title').textContent = spectatorOnly ? `Watch ${room.friendlyName}` : `Join ${room.friendlyName}`;
  document.querySelector('#room-code').textContent = `Room ${code}`;
  document.title = `PnPInk Play - ${room.friendlyName}`;
}).catch(reason => { errorNode.textContent = reason; });

document.querySelector('#join-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch(`/invites/${code}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.querySelector('#name').value}),
  });
  if (!response.ok) { errorNode.textContent = await response.text(); return; }
  remember(await response.json());
});

document.querySelector('#spectate-button').addEventListener('click', async () => {
  const response = await fetch(`/invites/${code}/spectate`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.querySelector('#name').value || 'Spectator'}),
  });
  if (!response.ok) { errorNode.textContent = await response.text(); return; }
  remember(await response.json());
});

