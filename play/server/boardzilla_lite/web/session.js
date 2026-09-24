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
let historyStatus;
let historyData;
let historyFolder;
let checkpointButtonController;
let checkpointInputController;
let checkpointEditing = false;
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

function buttonRow(actions, className, parent = serverMenu) {
  let first;
  for (const action of actions) {
    menuState[action.key] = action.number ? action.value : action.run;
    if (action.options) menuState[action.key] = action.value;
    const controller = first
      ? first.append(menuState, action.key, ...(action.options ? [action.options] : action.number ? [0, action.maximum || 0, 1] : []))
      : parent.add(menuState, action.key, ...(action.options ? [action.options] : action.number ? [0, action.maximum || 0, 1] : []));
    if (action.number) {
      controller.name('').onFinishChange(action.onChange);
      controller.domElement.title = action.title;
      controller.domElement.setAttribute('aria-label', action.title);
      controller.domElement.classList.add('server-menu-history-number');
    } else if (action.options) {
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
  row.classList.add('server-menu-row', ...className.split(/\s+/).filter(Boolean));
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
  if (details) details.canUndo = canUndo;
  if (undoButton) undoButton.hidden = !details?.allowUndo || !canUndo;
}

async function undoLatestMove() {
  const response = await fetch(`/sessions/${token}/undo`, {method: 'POST'});
  notify(response.ok ? 'Move undone' : await response.text());
}

function updateHistory(history) {
  if (!history) return;
  historyData = history;
  if (historyStatus) historyStatus.textContent = `History ${history.turnIndex}/${history.turnTotal} (${history.currentBranch || '1'})`;
  setEnabled(menuControllers.historyStart, history.canBack);
  setEnabled(menuControllers.historyBackTurn, history.canBack);
  setEnabled(menuControllers.historyBackAction, history.canBack);
  setEnabled(menuControllers.historyForwardAction, history.canForward);
  setEnabled(menuControllers.historyForwardTurn, history.canForward);
  setEnabled(menuControllers.historyEnd, history.canForward);
  for (const [key, value, maximum] of [
    ['historyAction', history.actionIndex, history.actionTotal],
    ['historyTurn', history.turnIndex, history.turnTotal],
    ['historyRound', history.roundIndex, history.roundTotal],
  ]) {
    const controller = menuControllers[key];
    if (!controller) continue;
    menuState[key] = value;
    controller.max(maximum).updateDisplay();
  }
  if (menuControllers.historyRoundRow) menuControllers.historyRoundRow.hidden = !history.roundTotal;
  if (menuControllers.historyBranch) {
    const options = {};
    const checkpoints = history.checkpoints || [];
    for (const branch of history.branches || []) {
      options[branch.path === '1' ? '1  Main' : `${branch.path}  Fork`] = branch.nodeID == null ? 'start' : `branch:${branch.nodeID}`;
      for (const checkpoint of checkpoints.filter(item => item.path === branch.path)) {
        options[`${checkpoint.name} (${checkpoint.path})`] = checkpoint.nodeID == null ? 'start' : `node:${checkpoint.nodeID}`;
      }
    }
    for (const checkpoint of checkpoints.filter(item => !(history.branches || []).some(branch => branch.path === item.path))) {
      options[`${checkpoint.name} (${checkpoint.path})`] = checkpoint.nodeID == null ? 'start' : `node:${checkpoint.nodeID}`;
    }
    if (!Object.keys(options).length) options['1  Main'] = 'start';
    menuControllers.historyBranch.options(options).onChange(navigateHistoryPoint);
    menuControllers.historyBranch.domElement.classList.add('server-menu-history-select');
    const checkpoint = checkpoints.find(item => item.nodeID === history.currentNodeID);
    const branch = (history.branches || []).find(item => item.path === history.currentBranch);
    menuState.historyBranch = checkpoint
      ? checkpoint.nodeID == null ? 'start' : `node:${checkpoint.nodeID}`
      : branch ? branch.nodeID == null ? 'start' : `branch:${branch.nodeID}` : Object.values(options)[0];
    menuControllers.historyBranch.updateDisplay();
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
  viewedPosition = details.viewPosition === -1
    ? details.currentPlayers?.[0]
    : details.viewPosition ?? details.position;
}

async function navigateHistory(command) {
  const response = await fetch(`/sessions/${token}/history`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command}),
  });
  if (!response.ok) return notify(await response.text());
  updateHistory((await response.json()).history);
}

async function navigateHistoryValue(command, value) {
  const response = await fetch(`/sessions/${token}/history`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({command, branchID: Math.max(0, Number(value) || 0)}),
  });
  if (!response.ok) return notify(await response.text());
  updateHistory((await response.json()).history);
}

async function navigateHistoryPoint(value) {
  if (!value) return;
  const response = await fetch(`/sessions/${token}/history`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      command: value === 'start' ? 'start' : value.startsWith('branch:') ? 'branch' : 'node',
      branchID: value === 'start' ? undefined : Number(value.slice(value.indexOf(':') + 1)),
    }),
  });
  if (!response.ok) return notify(await response.text());
  updateHistory((await response.json()).history);
}

function editCheckpoint() {
  checkpointEditing = true;
  menuState.checkpointName = '';
  checkpointInputController.updateDisplay();
  checkpointButtonController.hide();
  checkpointInputController.show();
  requestAnimationFrame(() => checkpointInputController.$input.focus());
}

function cancelCheckpoint() {
  checkpointEditing = false;
  checkpointInputController.hide();
  checkpointButtonController.show();
}

async function saveCheckpoint() {
  if (!checkpointEditing) return;
  const name = menuState.checkpointName.trim();
  cancelCheckpoint();
  if (!name) return;
  const response = await fetch(`/sessions/${token}/history/checkpoints`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name}),
  });
  if (!response.ok) return notify(await response.text());
  updateHistory((await response.json()).history);
  notify('Checkpoint saved');
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
  titleElement.append(turnBadge, undoButton);

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
    historyFolder = serverMenu.addFolder('History');
    historyFolder.domElement.classList.add('server-menu-history-folder');
    historyFolder.$title.textContent = '';
    historyFolder.$title.insertAdjacentHTML('beforeend', PnPInkPlay.icon('book-open', 'History'));
    historyStatus = document.createElement('span');
    historyStatus.className = 'server-menu-history-status';
    historyFolder.$title.append(historyStatus);
    menuState.newCheckpoint = editCheckpoint;
    checkpointButtonController = historyFolder.append(menuState, 'newCheckpoint');
    checkpointButtonController.domElement.classList.add('server-menu-checkpoint-controller');
    controllerIcon(checkpointButtonController, 'bookmark-plus', 'Create checkpoint');
    menuState.checkpointName = '';
    checkpointInputController = historyFolder.append(menuState, 'checkpointName').name('');
    checkpointInputController.domElement.classList.add('server-menu-checkpoint-input');
    checkpointInputController.$input.placeholder = 'Checkpoint name';
    checkpointInputController.hide();
    for (const controller of [checkpointButtonController, checkpointInputController]) {
      controller.domElement.addEventListener('pointerdown', event => event.stopPropagation());
      controller.domElement.addEventListener('click', event => event.stopPropagation());
    }
    checkpointInputController.$input.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        saveCheckpoint();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        cancelCheckpoint();
      }
    });
    checkpointInputController.$input.addEventListener('blur', saveCheckpoint);
    buttonRow([
      {key: 'historyStart', icon: 'skip-back', title: 'First action', run: () => navigateHistory('start')},
      {key: 'historyBackAction', icon: 'chevron-left', title: 'Previous action', run: () => navigateHistory('back-action')},
      {key: 'historyAction', number: true, value: 0, title: 'Go to action', onChange: value => navigateHistoryValue('action', value)},
      {key: 'historyForwardAction', icon: 'chevron-right', title: 'Next action', run: () => navigateHistory('forward-action')},
      {key: 'historyEnd', icon: 'skip-forward', title: 'Latest action', run: () => navigateHistory('end')},
    ], 'server-menu-history server-menu-history-actions', historyFolder);
    buttonRow([
      {key: 'historyFirstTurn', icon: 'skip-back', title: 'First turn', run: () => navigateHistoryValue('turn', 0)},
      {key: 'historyBackTurn', icon: 'arrow-left', title: 'Previous turn', run: () => navigateHistory('back-turn')},
      {key: 'historyTurn', number: true, value: 0, title: 'Go to turn', onChange: value => navigateHistoryValue('turn', value)},
      {key: 'historyForwardTurn', icon: 'arrow-right', title: 'Next turn', run: () => navigateHistory('forward-turn')},
      {key: 'historyLastTurn', icon: 'skip-forward', title: 'Latest turn', run: () => navigateHistory('end')},
    ], 'server-menu-history server-menu-history-turns', historyFolder);
    menuControllers.historyRoundRow = buttonRow([
      {key: 'historyFirstRound', icon: 'skip-back', title: 'First round', run: () => navigateHistoryValue('round', 0)},
      {key: 'historyBackRound', icon: 'arrow-left', title: 'Previous round', run: () => navigateHistoryValue('round', Math.max(0, (historyData?.roundIndex || 0) - 1))},
      {key: 'historyRound', number: true, value: 0, title: 'Go to round', onChange: value => navigateHistoryValue('round', value)},
      {key: 'historyForwardRound', icon: 'arrow-right', title: 'Next round', run: () => navigateHistoryValue('round', Math.min(historyData?.roundTotal || 0, (historyData?.roundIndex || 0) + 1))},
      {key: 'historyLastRound', icon: 'skip-forward', title: 'Latest round', run: () => navigateHistoryValue('round', historyData?.roundTotal || 0)},
    ], 'server-menu-history server-menu-history-rounds', historyFolder);
    menuState.historyBranch = '';
    menuControllers.historyBranch = historyFolder.add(menuState, 'historyBranch', {'1  Main': 'start'}).name('').onChange(navigateHistoryPoint);
    menuControllers.historyBranch.domElement.classList.add('server-menu-history-select');
    controllerIcon(menuControllers.historyBranch, 'arrow-split-right-alt', 'Branches and checkpoints');
    historyFolder.close();
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
  const userSelect = document.querySelector('#setup-user');
  document.querySelector('#setup-game-name').textContent = details.game.friendlyName;
  userSelect.innerHTML = users.map(user =>
    `<option value="${PnPInkPlay.escapeHTML(user.token)}"${details.userID === user.id ? ' selected' : ''}>${PnPInkPlay.escapeHTML(user.name)}</option>`
  ).join('');
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
document.querySelector('#setup-share-user').addEventListener('click', () => {
  const user = PnPInkPlay.knownUsers().find(candidate => candidate.id === details.userID);
  if (user) copyText(`${location.origin}/?identity=${encodeURIComponent(user.token)}`, 'Private player link copied');
});
const setupUserDialog = document.querySelector('#setup-user-dialog');
document.querySelector('#setup-new-user').addEventListener('click', () => {
  document.querySelector('#setup-user-error').textContent = '';
  document.querySelector('#setup-new-user-name').value = '';
  if (setupUserDialog.showModal) setupUserDialog.showModal(); else setupUserDialog.setAttribute('open', '');
  document.querySelector('#setup-new-user-name').focus();
});
document.querySelector('#setup-cancel-user').addEventListener('click', () => setupUserDialog.close?.());
document.querySelector('#setup-user-form').addEventListener('submit', async event => {
  event.preventDefault();
  const response = await fetch('/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: document.querySelector('#setup-new-user-name').value}),
  });
  if (!response.ok) {
    document.querySelector('#setup-user-error').textContent = await response.text();
    return;
  }
  const {user} = await response.json();
  PnPInkPlay.setActiveUser(user);
  setupUserDialog.close?.();
  await switchSetupUser(user.token);
});
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
  if (message.type === 'gameRevision') { reloadGameFrame(); return; }
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

window.addEventListener('keydown', event => {
  if ((!event.ctrlKey && !event.metaKey) || event.altKey || event.repeat) return;
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
    || target?.isContentEditable) return;
  const key = event.key.toLowerCase();
  if (key === 'z' && details?.allowUndo && details?.canUndo) {
    event.preventDefault();
    undoLatestMove();
  } else if (key === 'y' && details?.administrator && historyData?.canForward) {
    event.preventDefault();
    navigateHistory('forward-action');
  }
});

start().catch(error => notify(String(error)));
