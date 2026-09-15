import { XGUI } from '/static/lil-xgui.min.js';

const token = location.pathname.split('/').pop();
const frame = document.querySelector('#ui');
const toast = document.querySelector('#toast');
const menuState = {};
const menuControllers = {};
let details;
let serverMenu;
let turnBadge;
let undoButton;
let appModeButton;
let installPrompt;
let historyStatus;
let historyHasBranches = false;
let uiReady = false;
let viewedPosition;
let deferredGameUpdate;
let reconnectTimer;
const setupIdentity = document.querySelector('#setup-identity');

function notify(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), 2200);
}

function controllerIcon(controller, icon, title) {
  controller.name('');
  controller.domElement.title = title;
  controller.domElement.setAttribute('aria-label', title);
  controller.$name.insertAdjacentHTML('afterbegin', PnPInkPlay.icon(icon, title));
  return controller;
}

function buttonRow(actions, className) {
  let first;
  for (const action of actions) {
    menuState[action.key] = action.run;
    if (action.options) menuState[action.key] = action.value;
    const controller = first
      ? first.append(menuState, action.key, ...(action.options ? [action.options] : []))
      : serverMenu.add(menuState, action.key, ...(action.options ? [action.options] : []));
    if (action.options) {
      controller.name('').onChange(action.onChange);
      controller.domElement.title = action.title;
      controller.domElement.setAttribute('aria-label', action.title);
      controller.domElement.classList.add('server-menu-perspective');
    } else {
      controllerIcon(controller, action.icon, action.title);
    }
    menuControllers[action.key] = controller;
    first ??= controller;
  }
  const row = first.$multi ?? first.domElement;
  row.classList.add('server-menu-row', className);
  return row;
}

function middleTruncate(value, maximum) {
  if (value.length <= maximum) return value;
  const left = Math.ceil((maximum - 3) / 2);
  const right = Math.floor((maximum - 3) / 2);
  return `${value.slice(0, left)}...${value.slice(-right)}`;
}

function playerColor(position, fallback = '#394759') {
  return details.players.find(player => player.position === position)?.color || fallback;
}

function readableText(background) {
  const value = background.replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(value)) return '#fff';
  const channels = [0, 2, 4].map(index => parseInt(value.slice(index, index + 2), 16));
  return channels[0] * .299 + channels[1] * .587 + channels[2] * .114 > 150 ? '#080808' : '#fff';
}

function updateTurn(currentPlayers) {
  if (!turnBadge) return;
  const current = details.players.find(player => currentPlayers?.includes(player.position));
  const background = details.phase === 'finished' ? '#5b5272' : playerColor(current?.position);
  const viewerTurn = current?.position === details.position;
  const label = details.phase === 'finished' ? 'Finished' : viewerTurn ? 'Your turn...' : current?.name || 'Free play';
  const badgeBackground = viewerTurn || details.phase === 'finished' || !current ? '#050505' : background;
  const badgeText = readableText(badgeBackground);
  turnBadge.innerHTML = `${PnPInkPlay.icon(details.phase === 'finished' ? 'check' : 'user', label)}<span>${PnPInkPlay.escapeHTML(label)}</span>`;
  turnBadge.title = details.phase === 'finished' ? 'Game finished' : current ? `${current.name}'s turn` : 'Free play';
  turnBadge.style.background = badgeBackground;
  turnBadge.style.color = badgeText;
  serverMenu.domElement.classList.toggle('viewer-turn', Boolean(viewerTurn));
  serverMenu.domElement.style.setProperty('--turn-color', viewerTurn ? background : '#050505');
  serverMenu.domElement.style.setProperty('--turn-text', viewerTurn ? readableText(background) : '#fff');
}

function setEnabled(controller, enabled) {
  if (!controller) return;
  if (enabled) controller.enable();
  else controller.disable();
}

function updateUndo(canUndo) {
  if (undoButton) undoButton.hidden = !details?.allowUndo || !canUndo;
}

function installedApp() {
  return window.matchMedia('(display-mode: fullscreen)').matches
    || window.matchMedia('(display-mode: standalone)').matches
    || navigator.standalone === true;
}

function updateAppModeButton() {
  if (!appModeButton) return;
  const fullscreenAvailable = Boolean(document.documentElement.requestFullscreen) && !document.fullscreenElement;
  const iosInstall = /iPad|iPhone|iPod/.test(navigator.userAgent) && !installedApp();
  appModeButton.hidden = installedApp() || (!installPrompt && !fullscreenAvailable && !iosInstall);
  const installAvailable = Boolean(installPrompt) || iosInstall;
  appModeButton.innerHTML = PnPInkPlay.icon(installAvailable ? 'download' : 'fullscreen', installAvailable ? 'Install PnPPlay' : 'Enter fullscreen');
  appModeButton.title = installAvailable ? 'Install PnPPlay' : 'Enter fullscreen';
  appModeButton.setAttribute('aria-label', appModeButton.title);
}

async function enterAppMode() {
  if (installPrompt) {
    const prompt = installPrompt;
    installPrompt = undefined;
    await prompt.prompt();
    const choice = await prompt.userChoice;
    if (choice.outcome === 'accepted') notify('PnPPlay installed');
    updateAppModeButton();
    return;
  }
  if (/iPad|iPhone|iPod/.test(navigator.userAgent) && !installedApp()) {
    notify('Use Share, then Add to Home Screen');
    return;
  }
  try {
    await document.documentElement.requestFullscreen({navigationUI: 'hide'});
  } catch (_error) {
    notify('Fullscreen is unavailable in this browser');
  }
  updateAppModeButton();
}

async function undoLatestMove() {
  const response = await fetch(`/sessions/${token}/undo`, {method: 'POST'});
  notify(response.ok ? 'Move undone' : await response.text());
}

function updateHistory(history) {
  if (!history) return;
  if (historyStatus) historyStatus.textContent = `${history.index}/${history.tip}${history.forks ? ` (${history.forks} forks)` : ''}`;
  setEnabled(menuControllers.historyStart, history.canBack);
  setEnabled(menuControllers.historyBackTurn, history.canBack);
  setEnabled(menuControllers.historyBackAction, history.canBack);
  setEnabled(menuControllers.historyForwardAction, history.canForward);
  setEnabled(menuControllers.historyForwardTurn, history.canForward);
  setEnabled(menuControllers.historyEnd, history.canForward);
  if (menuControllers.historyBranch) {
    historyHasBranches = history.branches.length > 1;
    const options = Object.fromEntries(history.branches.map((item, index) => [`Branch ${index + 1} / P${item.position}`, String(item.nodeID)]));
    menuControllers.historyBranch.options(options).show(history.branches.length > 1);
    if (history.branches.length) {
      menuState.historyBranch = String(history.branches[0].nodeID);
      menuControllers.historyBranch.updateDisplay();
    }
  }
}

async function copyText(value, success) {
  await navigator.clipboard.writeText(value);
  notify(success);
}

function privatePlayerLink() {
  if (!details.administrator || details.viewPosition == null) return location.href;
  const position = details.viewPosition === -1
    ? viewedPosition ?? details.currentPlayers?.[0]
    : details.viewPosition;
  const player = details.players.find(candidate => candidate.position === position);
  return player?.sessionURL ? new URL(player.sessionURL, location.origin).href : location.href;
}

async function setPerspective(value) {
  const response = await fetch(`/sessions/${token}/perspective`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({position: value === '' ? null : Number(value)}),
  });
  if (!response.ok) return notify(await response.text());
  details.viewPosition = value === '' ? null : Number(value);
  viewedPosition = undefined;
  reloadGameFrame();
}

async function navigateHistory(command) {
  const branchID = command === 'forward-action' && historyHasBranches
    ? Number(menuState.historyBranch)
    : undefined;
  const response = await fetch(`/sessions/${token}/history`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command, branchID}),
  });
  if (!response.ok) return notify(await response.text());
  updateHistory((await response.json()).history);
}

function buildServerMenu() {
  const viewer = details.players.find(player => player.position === details.position);
  const viewerColor = viewer?.color || '#4de3ad';
  const fullTitle = `${details.game.friendlyName} - ${details.playerName}`;
  serverMenu = new XGUI({autoPlace: false, title: middleTruncate(details.playerName, 30), width: 206, touchStyles: false});
  serverMenu.domElement.classList.add('server-menu');
  serverMenu.domElement.title = fullTitle;
  serverMenu.domElement.style.setProperty('--viewer-color', viewerColor);
  document.body.appendChild(serverMenu.domElement);

  const titleElement = serverMenu.domElement.querySelector(':scope > .title') || serverMenu.domElement.querySelector('.title');
  turnBadge = document.createElement('span');
  turnBadge.className = 'server-menu-turn';
  turnBadge.textContent = 'Waiting...';
  undoButton = document.createElement('button');
  undoButton.className = 'server-menu-title-undo';
  undoButton.title = 'Undo your latest move';
  undoButton.setAttribute('aria-label', undoButton.title);
  undoButton.innerHTML = PnPInkPlay.icon('undo', undoButton.title);
  undoButton.addEventListener('pointerdown', event => event.stopPropagation());
  undoButton.addEventListener('click', event => {
    event.stopPropagation();
    undoLatestMove();
  });
  appModeButton = document.createElement('button');
  appModeButton.className = 'server-menu-title-app';
  appModeButton.addEventListener('pointerdown', event => event.stopPropagation());
  appModeButton.addEventListener('click', event => {
    event.stopPropagation();
    enterAppMode();
  });
  titleElement.append(turnBadge, appModeButton, undoButton);
  updateAppModeButton();

  menuState.lobby = () => { location.href = '/'; };
  const primaryActions = [
    {key: 'lobby', icon: 'home', title: 'Go to lobby', run: menuState.lobby},
  ];
  if (details.administrator || details.spectator) {
    const perspectives = Object.fromEntries([
      ...(details.spectator ? [] : [['My perspective', '']]),
      ['Current player', '-1'],
      ...details.players.map(player => [`${player.position}: ${player.name}`, String(player.position)]),
    ]);
    primaryActions.push({
      key: 'perspective',
      title: 'Perspective',
      value: details.viewPosition == null ? '' : String(details.viewPosition),
      options: perspectives,
      onChange: setPerspective,
    });
  }
  primaryActions.push(
    {key: 'playerLink', icon: 'link-alt-02', title: 'Copy private player link', run: () => copyText(privatePlayerLink(), 'Private player link copied')},
    {key: 'invite', icon: 'share', title: 'Copy invitation link', run: () => copyText(`${location.origin}/join/${details.inviteCode}`, 'Invitation link copied')},
  );
  buttonRow(primaryActions, 'server-menu-actions');

  if (details.administrator) {
    const historyRow = buttonRow([
      {key: 'historyStart', icon: 'skip-back', title: 'First action', run: () => navigateHistory('start')},
      {key: 'historyBackTurn', icon: 'arrow-left', title: 'Previous turn', run: () => navigateHistory('back-turn')},
      {key: 'historyBackAction', icon: 'chevron-left', title: 'Previous action', run: () => navigateHistory('back-action')},
      {key: 'historyForwardAction', icon: 'chevron-right', title: 'Next action', run: () => navigateHistory('forward-action')},
      {key: 'historyForwardTurn', icon: 'arrow-right', title: 'Next turn', run: () => navigateHistory('forward-turn')},
      {key: 'historyEnd', icon: 'skip-forward', title: 'Latest action', run: () => navigateHistory('end')},
    ], 'server-menu-history');
    historyStatus = document.createElement('span');
    historyStatus.className = 'server-menu-history-status';
    historyRow.prepend(historyStatus);
    menuState.historyBranch = '';
    menuControllers.historyBranch = serverMenu.add(menuState, 'historyBranch', {'No branches': ''}).name('Branch').hide();
  }

  updateUndo(details.canUndo);
  updateHistory(details.history);
  updateTurn(details.currentPlayers);
  serverMenu.close();
}

function renderSetupIdentity() {
  const users = PnPInkPlay.knownUsers();
  const currentIdentity = users.find(user => user.id === details.userID);
  if (currentIdentity) PnPInkPlay.setActiveUser(currentIdentity);
  const connected = new Map((details.participants || details.players).map(player => [player.userID, player]));
  const userSelect = document.querySelector('#setup-user');
  document.querySelector('#setup-game-name').textContent = details.game.friendlyName;
  userSelect.innerHTML = users.map(user => {
    const participant = connected.get(user.id);
    const status = participant ? participant.name === details.playerName ? 'current' : 'joined' : 'available';
    return `<option value="${PnPInkPlay.escapeHTML(user.token)}"${details.userID === user.id ? ' selected' : ''}>${PnPInkPlay.escapeHTML(user.name)} · ${status}</option>`;
  }).join('');
  document.querySelector('#setup-local-status').innerHTML = users.map(user => {
    const participant = connected.get(user.id);
    return `<span class="${participant ? 'connected' : ''}" title="${PnPInkPlay.escapeHTML(user.name)}: ${participant ? participant.ready ? 'ready' : 'joined' : 'not in this room'}">${PnPInkPlay.icon(participant?.ready ? 'check' : 'user')} ${PnPInkPlay.escapeHTML(user.name)}</span>`;
  }).join('');
  setupIdentity.hidden = false;
}

async function switchSetupUser(tokenValue) {
  const user = PnPInkPlay.knownUsers().find(candidate => candidate.token === tokenValue);
  if (!user || user.id === details.userID) return;
  const endpoint = details.spectator ? `/invites/${details.inviteCode}/spectate` : `/invites/${details.inviteCode}`;
  const response = await PnPInkPlay.authFetch(endpoint, {method: 'POST'}, user);
  if (!response.ok) {
    renderSetupIdentity();
    notify(await response.text());
    return;
  }
  const session = await response.json();
  PnPInkPlay.setActiveUser(user);
  PnPInkPlay.saveToken(session.sessionID, session.token);
  location.replace(`/s/${session.token}`);
}

async function refreshSetupIdentity() {
  if (details.phase !== 'new') return;
  const response = await fetch(`/sessions/${token}`);
  if (!response.ok) return;
  details = await response.json();
  renderSetupIdentity();
}

document.querySelector('#setup-user').addEventListener('change', event => switchSetupUser(event.target.value));
document.querySelector('#setup-copy-invite').addEventListener('click', () => copyText(`${location.origin}/join/${details.inviteCode}`, 'Invitation link copied'));

function updateRoomState(message) {
  const gameStarted = message.type === 'gameUpdate' && details.phase === 'new';
  if (message.type === 'gameUpdate') details.phase = 'started';
  if (message.type === 'gameFinished') details.phase = 'finished';
  if (message.type !== 'gameUpdate' && message.type !== 'gameFinished') return false;

  details.currentPlayers = message.currentPlayers;
  updateTurn(message.currentPlayers);
  updateUndo(message.canUndo);
  updateHistory(message.history);
  if (gameStarted) viewedPosition = message.position;
  if (gameStarted) {
    setupIdentity.hidden = true;
    if (!serverMenu) buildServerMenu();
  }
  if ((details.spectator || details.viewPosition === -1) && viewedPosition !== message.position) {
    viewedPosition = message.position;
    reloadGameFrame();
    return true;
  }
  return false;
}

function reloadGameFrame() {
  uiReady = false;
  frame.src = `/sessions/${token}/ui.html?v=${Date.now()}`;
}

function send(event) {
  if (uiReady) frame.contentWindow.postMessage(event, '*');
}

async function sendSnapshot() {
  const snapshot = await fetch(`/sessions/${token}/snapshot`).then(response => response.json());
  snapshot.events.forEach(event => {
    updateRoomState(event);
    send(event);
  });
}

async function command(path, body, id) {
  try {
    const response = await fetch(`/sessions/${token}${path}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const error = response.ok ? undefined : await response.text();
    if (error) await sendSnapshot();
    send({type: 'messageProcessed', id, error});
  } catch (error) {
    send({type: 'messageProcessed', id, error: String(error)});
  }
}

window.addEventListener('message', event => {
  if (event.source !== frame.contentWindow) return;
  const message = event.data || {};
  if (message.type === 'ready') {
    uiReady = true;
    sendSnapshot();
  } else if (message.type === 'updateSettings') {
    command('/settings', {settings: message.settings, seatCount: message.seatCount}, message.id);
  } else if (message.type === 'updatePlayers') {
    command('/players/operations', {operations: message.operations}, message.id);
  } else if (message.type === 'move') {
    if (!details.spectator) command('/moves', message.data, message.id);
  } else if (message.type === 'sendDark') {
    send({type: 'darkSetting', dark: false});
  }
});

function receive(message) {
  if (message.type === 'ping') return;
  if (message.type === 'roomDeleted') { location.href = '/'; return; }
  if (message.type === 'users' && details.phase === 'new') refreshSetupIdentity();
  if (document.hidden && (message.type === 'gameUpdate' || message.type === 'gameFinished')) {
    deferredGameUpdate = message;
    return;
  }
  if (!updateRoomState(message)) send(message);
}

function connectEventSource() {
  const stream = new EventSource(`/sessions/${token}/events`);
  stream.onmessage = event => receive(JSON.parse(event.data));
  stream.onerror = () => notify('Reconnecting...');
}

function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/sessions/${token}/socket`);
  let opened = false;
  socket.onopen = () => { opened = true; };
  socket.onmessage = event => receive(JSON.parse(event.data));
  socket.onclose = () => {
    if (!opened) return connectEventSource();
    notify('Reconnecting...');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectSocket, 1000);
  };
}

async function start() {
  const response = await fetch(`/sessions/${token}`);
  if (!response.ok) { location.href = '/'; return; }
  details = await response.json();
  document.body.classList.toggle('spectator-view', details.spectator);
  PnPInkPlay.saveToken(details.sessionID, token);
  document.title = `PnPPlay: ${details.game.friendlyName} - ${details.playerName}`;
  viewedPosition = details.effectivePosition;
  if (details.phase === 'new') renderSetupIdentity();
  else buildServerMenu();
  reloadGameFrame();
  connectSocket();
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden || !deferredGameUpdate) return;
  const message = deferredGameUpdate;
  deferredGameUpdate = undefined;
  if (!updateRoomState(message)) send(message);
});

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installPrompt = event;
  updateAppModeButton();
});

window.addEventListener('appinstalled', () => {
  installPrompt = undefined;
  updateAppModeButton();
});

document.addEventListener('fullscreenchange', updateAppModeButton);

start().catch(error => notify(String(error)));
