import { XGUI } from './vendor/lil-xgui.min.js';
import { googleSheetFromDataTransfer } from './gsheet-drop.js';
import { SpritesheetEditor } from './spritesheet-editor.js';
import { appShell } from './app-shell.js';
import { PreviewViewer } from './preview-viewer.js';
import { DatasetTable } from './dataset-table.js';

const materialIcon = name => `material-symbols-light:${name}`;
const ENABLE_SPRITESHEETS = true;
const PREVIEW_SYNC_MODES = {
  Off: 'off',
  'Dataset → Preview': 'dataset',
  'Two-way': 'two_way',
};

const query = new URLSearchParams(location.search);
const token = query.get('token') || sessionStorage.getItem('deckmaker-token') || '';
if (token) sessionStorage.setItem('deckmaker-token', token);
const managedAppWindow = query.get('shell') === 'app';
const browserSession = globalThis.crypto?.randomUUID?.()
  || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

const elements = {
  log: document.querySelector('#log'),
  toast: document.querySelector('#toast'),
  consolePanel: document.querySelector('#console-panel'),
  sheetEditor: document.querySelector('#gsheet-editor'),
  sheetEditorFrame: document.querySelector('#gsheet-editor-frame'),
  sheetEditorMessage: document.querySelector('#gsheet-editor-message'),
  datasetTable: document.querySelector('#dataset-table'),
  previewViewport: document.querySelector('#preview-viewport'),
  previewStage: document.querySelector('#preview-stage'),
  previewImage: document.querySelector('#preview-image'),
  previewMessage: document.querySelector('#preview-message'),
  previewScale: document.querySelector('#preview-scale'),
  spriteGridOverlay: document.querySelector('#sprite-grid-overlay'),
  pdfPageControls: document.querySelector('#pdf-page-controls'),
  pdfPagePrevious: document.querySelector('#pdf-page-previous'),
  pdfPageNext: document.querySelector('#pdf-page-next'),
  pdfPageLabel: document.querySelector('#pdf-page-label'),
  workspace: document.querySelector('main'),
  splitter: document.querySelector('#workspace-splitter'),
  spritesheetGui: document.querySelector('#spritesheet-gui'),
  logClose: document.querySelector('#log-close'),
  jobStatus: document.querySelector('#job-status'),
  jobSpinner: document.querySelector('#job-spinner'),
  jobTitle: document.querySelector('#job-title'),
  jobActivity: document.querySelector('#job-activity'),
  googleAuthLink: document.querySelector('#google-auth-link'),
  jobProgress: document.querySelector('#job-progress'),
};

const model = {
  previewSelectionSync: 'off',
  spriteSource: 'No selection',
  spriteMode: 'Grid',
  spriteOrder: 'Rows first',
  spriteAlias: 'sprites',
  spriteDpi: 300,
  spriteCols: 6,
  spriteRows: 4,
  spritePreset: 'Standard',
  spriteWidth: 63,
  spriteHeight: 88,
  spriteGapH: 0,
  spriteGapV: 0,
  spriteTop: 0,
  spriteRight: 0,
  spriteBottom: 0,
  spriteLeft: 0,
  spriteSizing: 'Grid',
  spriteLinkGaps: true,
  spriteLinkHorizontalBorders: true,
  spriteLinkVerticalBorders: true,
};
const previewViewer = new PreviewViewer({
  viewport: elements.previewViewport,
  stage: elements.previewStage,
  image: elements.previewImage,
  message: elements.previewMessage,
  scale: elements.previewScale,
  overlays: [elements.spriteGridOverlay],
}, token);
const datasetTable = new DatasetTable({
  container: elements.datasetTable,
  message: elements.sheetEditorMessage,
  reportError: message => toast(message),
  reportStatus: message => toast(message),
}, path => api(path), values => api('/api/dataset/cells', {
  method: 'POST',
  body: JSON.stringify(values),
}), values => api('/api/dataset/structure', {
  method: 'POST',
  body: JSON.stringify(values),
}), selection => schedulePreviewCellSync(selection), () => cancelPreviewCellSync());
const controllers = [];
const actionControllers = [];
let sequence = 0;
let built = false;
let spriteEditor;
let gui;
let datasetGui;
let datasetDockController;
let datasetTitleLabel;
let datasetDock = 'bottom';
const splitPositions = { bottom: .58, right: .55 };
let datasetLayoutSaveTimer;
let toolsFolder;
let spriteGui;
let spriteCopyDefinitionController;
let spriteCustomRow;
let spriteSequence = 0;
let spriteLayoutBefore = null;
let templateSelect;
let templateRefresh;
let templateName;
let previewPrevious;
let previewNext;
let previewAssetSelect;
let latestState = {};
let lastStatus = '';
let logDisplayMode = 'auto';
let wasBusy = false;
let splitterReady = false;
let sheetConfigured = false;
let installController;
let fullscreenController;
let playServerController;
let openSheetController;
let googleSheetToCsvController;
let sheetEditorActive = false;
let sheetActivitySentAt = 0;
let sheetActivityChain = Promise.resolve();
let sheetViewMode = 'minimal';
let toastTimer = 0;
let lastWindowGeometry = '';
let geometryTimer = 0;
let windowGeometryReady = !managedAppWindow;
let windowGeometryRestored = false;
let previewCellSyncTimer = 0;
let previewSelectionPolling = false;
let lastPreviewSelection = '';

function leaveSpritesheet() {
  if (!spriteEditor?.active) return;
  spriteEditor?.deactivate();
  elements.spritesheetGui.hidden = true;
  elements.workspace.classList.remove('spritesheet-mode');
  if (spriteLayoutBefore) {
    if (datasetDock === spriteLayoutBefore.dock) {
      if (datasetDock === 'right') setDeckMakerPaneWidth(spriteLayoutBefore.datasetWidth, false);
      else setDeckMakerPaneHeight(spriteLayoutBefore.previewHeight, false);
    }
    spriteLayoutBefore = null;
  }
  previewViewer.setPreview(latestState.preview);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-DeckMaker-Token': token,
      'X-DeckMaker-Session': browserSession,
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function toast(message) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add('visible');
  toastTimer = setTimeout(() => elements.toast.classList.remove('visible'), 3200);
}

function schedulePreviewCellSync(selection) {
  const hasPreviewAssets = Array.isArray(latestState.preview?.assets)
    && latestState.preview.assets.length > 0;
  if (model.previewSelectionSync === 'off' && hasPreviewAssets) return;
  clearTimeout(previewCellSyncTimer);
  previewCellSyncTimer = setTimeout(async () => {
    try {
      const result = await api('/api/preview/sync', {
        method: 'POST',
        body: JSON.stringify(selection),
      });
      const synced = result.selection;
      if (synced) {
        lastPreviewSelection = `${synced.asset}:${synced.row}:${synced.column}`;
      }
    } catch (error) {
      toast(error.message);
    }
  }, 300);
}

function cancelPreviewCellSync() {
  clearTimeout(previewCellSyncTimer);
  previewCellSyncTimer = 0;
}

async function pollInkscapeSelection() {
  if (previewSelectionPolling || document.hidden || document.hasFocus()
      || model.previewSelectionSync !== 'two_way'
      || latestState.preview?.backend !== 'inkscape' || latestState.datasetEditor?.kind !== 'csv') return;
  previewSelectionPolling = true;
  try {
    const result = await api('/api/preview/selection');
    const selection = result.selection;
    if (!selection) return;
    const signature = `${selection.asset}:${selection.row}:${selection.column}`;
    if (signature === lastPreviewSelection) return;
    lastPreviewSelection = signature;
    await datasetTable.selectCell(selection.row, selection.column);
  } catch (_error) {
    // The preview window may be closing or reloading; the next poll will retry.
  } finally {
    previewSelectionPolling = false;
  }
}

setInterval(pollInkscapeSelection, 400);

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {}
  }
  const input = document.createElement('textarea');
  input.value = text;
  input.style.position = 'fixed';
  input.style.left = '-9999px';
  input.style.top = '0';
  document.body.append(input);
  input.focus();
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  if (!copied) throw new Error('Browser denied clipboard access');
}

async function copyTemplateHeaders() {
  try {
    const result = await api('/api/template/headers');
    await copyText(result.text || '');
    toast(`Copied ${result.headers?.length || 0} template headers`);
  } catch (error) {
    toast(`Could not copy headers: ${error.message}`);
  }
}

function reportSheetActivity(active, force = false) {
  const now = Date.now();
  if (!force && active === sheetEditorActive && (!active || now - sheetActivitySentAt < 2000)) return;
  sheetEditorActive = active;
  sheetActivitySentAt = now;
  sheetActivityChain = sheetActivityChain.then(() => api('/api/sheet-activity', {
    method: 'POST',
    body: JSON.stringify({ active }),
  })).catch(() => {});
}

function updateSheetActivity(force = false) {
  const active = document.visibilityState === 'visible'
    && !elements.sheetEditorFrame.hidden
    && document.activeElement === elements.sheetEditorFrame;
  reportSheetActivity(active, force || active !== sheetEditorActive);
}

setInterval(() => updateSheetActivity(), 250);
window.addEventListener('focus', () => setTimeout(() => updateSheetActivity(true), 0));
window.addEventListener('blur', () => setTimeout(() => updateSheetActivity(true), 0));
document.addEventListener('visibilitychange', () => updateSheetActivity(true));

function currentWindowGeometry() {
  if (!managedAppWindow || !windowGeometryReady) return null;
  const geometry = {
    x: Math.round(window.screenX),
    y: Math.round(window.screenY),
    width: Math.round(window.outerWidth),
    height: Math.round(window.outerHeight),
  };
  return Object.values(geometry).every(Number.isFinite) ? geometry : null;
}

function restoreWindowGeometry(geometry) {
  if (!managedAppWindow || windowGeometryRestored) return;
  windowGeometryRestored = true;
  const values = ['x', 'y', 'width', 'height'].map(key => Number(geometry?.[key]));
  if (values.every(Number.isFinite) && values[2] >= 320 && values[3] >= 240) {
    const apply = () => {
      try {
        window.moveTo(values[0], values[1]);
        window.resizeTo(values[2], values[3]);
      } catch (_error) {
        // Chromium may apply the launch geometry itself or reject script-driven placement.
      }
    };
    apply();
    setTimeout(apply, 150);
    setTimeout(apply, 500);
  }
  setTimeout(() => {
    windowGeometryReady = true;
    reportWindowGeometry(true);
  }, 750);
}

function reportWindowGeometry(force = false) {
  const geometry = currentWindowGeometry();
  if (!geometry) return;
  const signature = JSON.stringify(geometry);
  if (!force && signature === lastWindowGeometry) return;
  lastWindowGeometry = signature;
  api('/api/window-geometry', {
    method: 'POST',
    body: JSON.stringify({ geometry }),
  }).catch(() => {});
}

function scheduleWindowGeometryReport() {
  clearTimeout(geometryTimer);
  geometryTimer = setTimeout(() => reportWindowGeometry(), 200);
}

if (managedAppWindow) {
  setInterval(() => reportWindowGeometry(), 1000);
  window.addEventListener('resize', scheduleWindowGeometryReport);
  window.addEventListener('focus', () => reportWindowGeometry());
}

let browserSessionClosed = false;

function closeBrowserSession() {
  if (browserSessionClosed) return;
  browserSessionClosed = true;
  const params = new URLSearchParams({ token, session: browserSession });
  const body = JSON.stringify({ geometry: currentWindowGeometry() });
  navigator.sendBeacon?.(`/api/session/close?${params}`, new Blob([body], { type: 'application/json' }));
  fetch('/api/session/close', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-DeckMaker-Token': token,
      'X-DeckMaker-Session': browserSession,
    },
    body,
    keepalive: true,
  }).catch(() => {});
}

window.addEventListener('pagehide', closeBrowserSession);
window.addEventListener('beforeunload', closeBrowserSession);

function reportBrowserVisibility() {
  return api('/api/session/activity', {
    method: 'POST',
    body: JSON.stringify({ visible: document.visibilityState === 'visible' }),
  });
}

function openBrowserSession() {
  return api('/api/session/open', {
    method: 'POST',
    body: JSON.stringify({ visible: document.visibilityState === 'visible' }),
  });
}

document.addEventListener('visibilitychange', () => reportBrowserVisibility().catch(() => {}));
window.addEventListener('pageshow', () => {
  browserSessionClosed = false;
  openBrowserSession()
    .then(result => restoreWindowGeometry(result.geometry))
    .catch(() => {
      windowGeometryReady = true;
    });
});
setInterval(() => reportBrowserVisibility().catch(() => {}), 5000);

function updateLogVisibility(busy = Boolean(latestState.busy)) {
  const quietJob = ['preview', 'gsheet-to-csv'].includes(String(latestState.job || ''));
  const visible = logDisplayMode === 'open' || (logDisplayMode === 'auto' && busy && !quietJob);
  elements.consolePanel.hidden = !visible;
  document.body.classList.toggle('log-visible', visible);
}

function toggleLog() {
  logDisplayMode = elements.consolePanel.hidden ? 'open' : 'closed';
  updateLogVisibility();
}

elements.logClose.addEventListener('click', () => {
  logDisplayMode = 'closed';
  updateLogVisibility();
});

function setDeckMakerPaneHeight(value, remember = true) {
  const minimum = Math.max(60, window.innerHeight * .1);
  const maximum = Math.max(minimum, window.innerHeight - minimum - 8);
  const height = Math.round(Math.max(minimum, Math.min(maximum, Number(value) || minimum)));
  elements.workspace.style.setProperty('--deckmaker-pane-height', `${height}px`);
  elements.splitter.setAttribute('aria-valuenow', String(height));
  if (remember) {
    const splitterHeight = elements.splitter.getBoundingClientRect().height || 8;
    splitPositions.bottom = (elements.workspace.clientHeight - height - splitterHeight)
      / Math.max(1, elements.workspace.clientHeight);
    scheduleDatasetLayoutSave();
  }
  updateSheetViewMode(datasetDock === 'bottom' && height <= window.innerHeight * .15 ? 'demo' : 'minimal');
  datasetTable.redraw();
}

function setDeckMakerPaneWidth(value, remember = true) {
  const minimum = Math.max(240, window.innerWidth * .2);
  const maximum = Math.max(minimum, window.innerWidth - 100);
  const width = Math.round(Math.max(minimum, Math.min(maximum, Number(value) || minimum)));
  elements.workspace.style.setProperty('--deckmaker-dataset-width', `${width}px`);
  elements.splitter.setAttribute('aria-valuenow', String(width));
  if (remember) {
    const splitterWidth = elements.splitter.getBoundingClientRect().width || 8;
    splitPositions.right = (elements.workspace.clientWidth - width - splitterWidth)
      / Math.max(1, elements.workspace.clientWidth);
    scheduleDatasetLayoutSave();
  }
  updateSheetViewMode('minimal');
  datasetTable.redraw();
}

function updateDatasetDockButton() {
  if (!datasetDockController || !gui) return;
  const moveRight = datasetDock === 'bottom';
  const tooltip = moveRight ? 'Move dataset to the right' : 'Move dataset to the bottom';
  const buttonContent = datasetDockController.$name || datasetDockController;
  const buttonElement = datasetDockController.domElement || datasetDockController;
  buttonContent.replaceChildren(gui._createIcon(materialIcon(moveRight ? 'dock-to-right' : 'dock-to-bottom')));
  buttonElement.title = tooltip;
  buttonElement.setAttribute('aria-label', tooltip);
}

function setDatasetDock(value, remember = true) {
  datasetDock = value === 'right' ? 'right' : 'bottom';
  elements.workspace.classList.toggle('dataset-dock-bottom', datasetDock === 'bottom');
  elements.workspace.classList.toggle('dataset-dock-right', datasetDock === 'right');
  elements.splitter.setAttribute('aria-orientation', datasetDock === 'right' ? 'vertical' : 'horizontal');
  elements.splitter.setAttribute('aria-label', `Resize preview and ${datasetDock === 'right' ? 'right' : 'bottom'} dataset`);
  if (remember) scheduleDatasetLayoutSave();
  updateDatasetDockButton();
  if (datasetDock === 'right') {
    const splitterWidth = elements.splitter.getBoundingClientRect().width || 8;
    setDeckMakerPaneWidth(
      elements.workspace.clientWidth * (1 - splitPositions.right) - splitterWidth,
      false,
    );
  } else {
    const splitterHeight = elements.splitter.getBoundingClientRect().height || 8;
    setDeckMakerPaneHeight(
      elements.workspace.clientHeight * (1 - splitPositions.bottom) - splitterHeight,
      false,
    );
  }
}

function sheetEditorUrl(state = latestState, mode = sheetViewMode) {
  const sheetId = String(state.sheetId || '').trim();
  const sheetGid = String(state.sheetGid ?? '').trim();
  if (!sheetId) return '';
  const base = `https://docs.google.com/spreadsheets/d/${encodeURIComponent(sheetId)}/edit?rm=${mode}`;
  return sheetGid
    ? `${base}&gid=${encodeURIComponent(sheetGid)}#gid=${encodeURIComponent(sheetGid)}`
    : base;
}

function openGoogleSheet() {
  const sheetId = String(model.sheetId || '').trim();
  if (!sheetId) {
    toast('Link a Google Sheet first');
    return;
  }
  const configuredRange = String(model.sheetRange || '').trim();
  const resolvedGid = String(latestState.sheetId || '').trim() === sheetId
    ? String(latestState.sheetGid ?? '').trim()
    : '';
  const gid = /^\d+$/.test(configuredRange) ? configuredRange : resolvedGid;
  const base = `https://docs.google.com/spreadsheets/d/${encodeURIComponent(sheetId)}/edit`;
  const url = gid ? `${base}#gid=${encodeURIComponent(gid)}` : base;
  window.open(url, '_blank', 'noopener,noreferrer');
}

function updateSheetViewMode(mode) {
  const next = mode === 'demo' ? 'demo' : 'minimal';
  if (sheetViewMode === next) return;
  sheetViewMode = next;
  const sheetId = String(latestState.sheetId || '').trim();
  if (!sheetId || latestState.datasetEditor?.kind !== 'gsheet') return;
  const sheetGid = String(latestState.sheetGid ?? '').trim();
  const sheetKey = `${sheetId}:${sheetGid}:${sheetViewMode}`;
  elements.sheetEditorFrame.dataset.sheetKey = sheetKey;
  elements.sheetEditorFrame.src = sheetEditorUrl();
}

function showMissingDatasetGuide() {
  const title = document.createElement('strong');
  title.textContent = 'This template has no dataset yet';
  const explanation = document.createElement('span');
  explanation.textContent = 'Create a starter CSV from the SVG fields, or drag a Google Sheets URL from your browser onto this area.';
  const create = document.createElement('button');
  create.type = 'button';
  create.className = 'deckmaker-create-dataset';
  create.textContent = 'Create starter CSV';
  create.title = 'Create a commented local CSV with headers inferred from the template bbox and its fields';
  create.disabled = !latestState.canCreateDataset || Boolean(latestState.busy);
  create.addEventListener('click', () => command('create-starter-csv'));
  elements.sheetEditorMessage.replaceChildren(title, explanation, create);
}

function initializeSplitter(hasSheet) {
  if (splitterReady) {
    if (!sheetConfigured && hasSheet) {
      if (datasetDock === 'right') {
        const splitterWidth = elements.splitter.getBoundingClientRect().width || 8;
        setDeckMakerPaneWidth(
          elements.workspace.clientWidth * (1 - splitPositions.right) - splitterWidth,
          false,
        );
      } else {
        const splitterHeight = elements.splitter.getBoundingClientRect().height || 8;
        setDeckMakerPaneHeight(
          elements.workspace.clientHeight * (1 - splitPositions.bottom) - splitterHeight,
          false,
        );
      }
    }
    sheetConfigured = hasSheet;
    return;
  }
  splitterReady = true;
  sheetConfigured = hasSheet;
  datasetDock = model.datasetDock === 'right' ? 'right' : 'bottom';
  const bottomPosition = Number(model.datasetSplitBottom);
  const rightPosition = Number(model.datasetSplitRight);
  splitPositions.bottom = bottomPosition > .05 && bottomPosition < .95
    ? bottomPosition
    : (hasSheet ? .58 : .18);
  splitPositions.right = rightPosition > .05 && rightPosition < .95
    ? rightPosition
    : .55;
  setDatasetDock(datasetDock, false);
}

async function persistDatasetLayout() {
  clearTimeout(datasetLayoutSaveTimer);
  datasetLayoutSaveTimer = undefined;
  const values = {
    datasetDock,
    datasetSplitBottom: splitPositions.bottom,
    datasetSplitRight: splitPositions.right,
  };
  Object.assign(model, values);
  try {
    await api('/api/settings', { method: 'POST', body: JSON.stringify(values) });
  } catch (error) {
    toast(error.message);
  }
}

function scheduleDatasetLayoutSave() {
  clearTimeout(datasetLayoutSaveTimer);
  datasetLayoutSaveTimer = setTimeout(persistDatasetLayout, 250);
}

elements.splitter.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  event.preventDefault();
  elements.splitter.setPointerCapture?.(event.pointerId);
  const move = moveEvent => {
    const bounds = elements.workspace.getBoundingClientRect();
    if (datasetDock === 'right') setDeckMakerPaneWidth(bounds.right - moveEvent.clientX);
    else setDeckMakerPaneHeight(moveEvent.clientY - bounds.top);
  };
  const finish = () => {
    elements.splitter.removeEventListener('pointermove', move);
    elements.splitter.removeEventListener('pointerup', finish);
    elements.splitter.removeEventListener('pointercancel', finish);
    persistDatasetLayout();
  };
  elements.splitter.addEventListener('pointermove', move);
  elements.splitter.addEventListener('pointerup', finish);
  elements.splitter.addEventListener('pointercancel', finish);
});

elements.splitter.addEventListener('keydown', event => {
  if (datasetDock === 'right') {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-dataset-width')) || 500;
    setDeckMakerPaneWidth(current + (event.key === 'ArrowLeft' ? 16 : -16));
    return;
  }
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
  event.preventDefault();
  const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-pane-height')) || 300;
  setDeckMakerPaneHeight(current + (event.key === 'ArrowDown' ? 16 : -16));
});

elements.splitter.addEventListener('dblclick', event => {
  event.preventDefault();
  if (datasetDock === 'right') {
    const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-dataset-width'))
      || window.innerWidth * .5;
    setDeckMakerPaneWidth(window.innerWidth * (current > window.innerWidth * .5 ? .1 : .9));
    return;
  }
  const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-pane-height'))
    || window.innerHeight * .5;
  setDeckMakerPaneHeight(window.innerHeight * (current > window.innerHeight * .5 ? .1 : .9));
});

window.addEventListener('resize', () => {
  if (datasetDock === 'right') {
    const splitterWidth = elements.splitter.getBoundingClientRect().width || 8;
    setDeckMakerPaneWidth(
      elements.workspace.clientWidth * (1 - splitPositions.right) - splitterWidth,
      false,
    );
  } else {
    const splitterHeight = elements.splitter.getBoundingClientRect().height || 8;
    setDeckMakerPaneHeight(
      elements.workspace.clientHeight * (1 - splitPositions.bottom) - splitterHeight,
      false,
    );
  }
});

async function save(key, value) {
  try {
    if (key === 'sourceMode') await datasetTable.flushEdits();
    applyState(await api('/api/settings', { method: 'POST', body: JSON.stringify({ [key]: value }) }));
  } catch (error) {
    toast(error.message);
  }
}

async function command(name) {
  if (name === 'generate' || name === 'preview' || name.startsWith('preview-')) leaveSpritesheet();
  try {
    const result = await api('/api/command', { method: 'POST', body: JSON.stringify({ command: name }) });
    if (result.state) applyState(result.state);
    if (!result.accepted) toast(result.state?.status || 'Action is not available');
  } catch (error) {
    toast(error.message);
  }
}

function decorate(controller, { label = '', icon = '', image = '', tooltip = '', iconOnly = false, className = '' } = {}) {
  controller.name(iconOnly ? '' : label);
  if (icon) controller.icon(icon.includes(':') ? icon : materialIcon(icon));
  if (image && controller.$name) {
    const element = document.createElement('img');
    element.className = 'deckmaker-controller-icon';
    element.src = image;
    element.alt = '';
    controller.$name.prepend(element);
  }
  if (tooltip) {
    controller.domElement.title = tooltip;
    controller.domElement.setAttribute('aria-label', tooltip);
  }
  if (iconOnly) controller.domElement.classList.add('deckmaker-icon-only');
  if (className) controller.domElement.classList.add(...className.split(/\s+/).filter(Boolean));
  return controller;
}

function finishRow(first, className = '') {
  const row = first?.$multi || first?.domElement;
  row?.classList.add('deckmaker-row', ...className.split(/\s+/).filter(Boolean));
  return row;
}

function menuTitle(state) {
  return state.version || 'Deckmaker';
}

function datasetTitle(state) {
  const editorKind = String(state.datasetEditor?.kind || '').toLowerCase();
  const sourceMode = String(state.sourceMode || '').toLowerCase();
  if (editorKind === 'csv' || sourceMode.includes('csv') || sourceMode === 'local') return 'Dataset [CSV]';
  if (editorKind === 'gsheet' || sourceMode.includes('google') || sourceMode.includes('oauth') || sourceMode.includes('public')) {
    return 'Dataset [GSheet]';
  }
  return 'Dataset';
}

function updateMenuIdentity(state) {
  updateTemplateTitle(state);
  updatePreviewNavigation(state);
  if (datasetGui?.$title) {
    if (datasetTitleLabel) datasetTitleLabel.textContent = datasetTitle(state);
    datasetGui.$title.title = `${state.datasetPath || 'No dataset'}\n\nDrag a Google Sheets URL here to link it automatically.`;
  }
  const title = `${menuTitle(state)} [${state.templateName || 'No template'}]`;
  document.title = title;
}

function titleButton(titleBar, className, text, tooltip, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `deckmaker-title-button ${className}`;
  button.textContent = text;
  button.title = tooltip;
  button.addEventListener('pointerdown', event => event.stopPropagation());
  button.addEventListener('click', event => {
    event.stopPropagation();
    handler();
  });
  titleBar.append(button);
  return button;
}

function updateTemplateTitle(state) {
  if (!gui?.$title) return;
  const titleBar = gui.$multi || gui.$title;
  if (!templateSelect) {
    [...gui.$title.childNodes]
      .filter(node => node.nodeType === Node.TEXT_NODE)
      .forEach(node => node.remove());
    templateName = document.createElement('span');
    templateName.className = 'deckmaker-template-name';
    gui.$title.append(templateName);
    gui.$title.classList.add('deckmaker-root-toggle');
    const picker = document.createElement('span');
    picker.className = 'deckmaker-template-picker';
    picker.title = 'Select another known SVG template';
    picker.append(gui._createIcon(materialIcon('swap-horiz')));
    templateSelect = document.createElement('select');
    templateSelect.className = 'deckmaker-template-select';
    templateSelect.title = 'Known SVG templates. Launch DeckMaker Web from Inkscape to register another template.';
    templateSelect.addEventListener('click', event => event.stopPropagation());
    templateSelect.addEventListener('pointerdown', event => event.stopPropagation());
    templateSelect.addEventListener('change', event => {
      event.stopPropagation();
      leaveSpritesheet();
      save('templatePath', templateSelect.value);
    });
    picker.append(templateSelect);
    titleBar.append(picker);
  }
  templateName.textContent = state.templateName || 'No template';
  const records = Array.isArray(state.templates) ? state.templates : [];
  const signature = records.map(record => `${record.path}\u0000${record.name}`).join('\u0001');
  if (templateSelect.dataset.signature !== signature) {
    templateSelect.replaceChildren(...records.map(record => {
      const option = document.createElement('option');
      option.value = record.path;
      option.textContent = record.name;
      option.title = record.path;
      return option;
    }));
    templateSelect.dataset.signature = signature;
  }
  templateSelect.value = state.template || '';
  templateSelect.disabled = !records.length || Boolean(state.busy);
  if (templateRefresh) templateRefresh.disabled = !state.canGenerate || Boolean(state.busy);
  gui.$title.title = state.template || '';
}

function updatePreviewNavigation(state) {
  if (!previewAssetSelect) return;
  const preview = state.preview || {};
  const assets = Array.isArray(preview.assets) ? preview.assets : [];
  const assetSignature = assets.map(asset => `${asset.value}\u0000${asset.label}`).join('\u0001');
  if (previewAssetSelect.dataset.signature !== assetSignature) {
    previewAssetSelect.replaceChildren(...assets.map(asset => {
      const option = document.createElement('option');
      option.value = asset.value;
      option.textContent = asset.label;
      option.title = asset.label;
      return option;
    }));
    previewAssetSelect.dataset.signature = assetSignature;
  }
  previewAssetSelect.value = preview.asset || '';
  previewAssetSelect.disabled = !assets.length || Boolean(state.busy);
  previewPrevious.disabled = !assets.length || Boolean(state.busy);
  previewNext.disabled = !assets.length || Boolean(state.busy);
}

function settingRow(folder, items, className = '') {
  let first;
  for (const item of items) {
    const args = item.inputType
      ? [item.inputType, item.attributes || {}, ...(item.args || [])]
      : (item.args || []);
    const controller = first
      ? first.append(model, item.key, ...args)
      : folder.add(model, item.key, ...args);
    decorate(controller, item);
    const callback = item.onChange
      || (item.local ? () => spriteEditor?.requestUpdate() : value => save(item.key, value));
    (item.live ? controller.onChange(callback) : controller.onFinishChange(callback));
    if (item.local && typeof model[item.key] === 'number') enableSliderWheel(controller, item.args);
    controllers.push(controller);
    first ||= controller;
  }
  finishRow(first, className);
  return first;
}

function enableSliderWheel(controller, args = []) {
  const slider = controller.$slider || controller.domElement.querySelector('.slider');
  if (!slider) return;
  const [minimum = -Infinity, maximum = Infinity, configuredStep] = args;
  const step = Number(configuredStep) || 1;
  let accumulatedDelta = 0;
  let lastDirection = 0;
  let resetTimer;
  slider.addEventListener('wheel', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const rawDelta = Math.abs(event.deltaY) >= Math.abs(event.deltaX)
      ? event.deltaY
      : -event.deltaX;
    const unitScale = event.deltaMode === WheelEvent.DOM_DELTA_LINE
      ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? 100 : 1;
    const delta = rawDelta * unitScale;
    const direction = Math.sign(delta);
    if (!direction) return;
    if (lastDirection && direction !== lastDirection) accumulatedDelta = 0;
    lastDirection = direction;
    accumulatedDelta += delta;
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      accumulatedDelta = 0;
      lastDirection = 0;
    }, 180);
    if (Math.abs(accumulatedDelta) < 40) return;
    accumulatedDelta = 0;
    const increment = direction < 0 ? step : -step;
    const value = Math.max(minimum, Math.min(maximum, Number(controller.getValue()) + increment));
    controller.setValue(Number(value.toFixed(10)));
  }, { passive: false, capture: true });
}

function labeledSettingRow(folder, label, items, className = '') {
  const first = settingRow(folder, items, className);
  const row = first?.$multi || first?.domElement;
  if (row) {
    const element = document.createElement('span');
    element.className = 'deckmaker-row-label';
    element.textContent = label;
    row.prepend(element);
  }
  return first;
}

function updateSpriteLinkButton(controller, linked, tooltip) {
  const iconHost = controller?.$name;
  if (iconHost && spriteGui) {
    iconHost.replaceChildren(spriteGui._createIcon(materialIcon(linked ? 'link' : 'link-off')));
  }
  if (controller?.domElement) {
    controller.domElement.classList.toggle('linked', linked);
    controller.domElement.title = tooltip;
    controller.domElement.setAttribute('aria-label', tooltip);
  }
}

function linkedSpriteRow(label, firstItem, secondItem, linkKey) {
  let synchronizing = false;
  let firstController;
  let secondController;
  const apply = (sourceKey, targetKey, controller, value) => {
    if (synchronizing) return;
    model[sourceKey] = value;
    if (model[linkKey]) {
      synchronizing = true;
      model[targetKey] = value;
      controller.setValue(value);
      synchronizing = false;
    }
    spriteEditor?.requestUpdate();
  };
  firstController = spriteGui.add(model, firstItem.key, ...firstItem.args);
  decorate(firstController, firstItem);
  secondController = firstController.append(model, secondItem.key, ...secondItem.args);
  decorate(secondController, secondItem);
  firstController.onChange(value => apply(firstItem.key, secondItem.key, secondController, value));
  secondController.onChange(value => apply(secondItem.key, firstItem.key, firstController, value));
  enableSliderWheel(firstController, firstItem.args);
  enableSliderWheel(secondController, secondItem.args);
  controllers.push(firstController, secondController);

  const linkAction = { toggleSpriteLink: () => {
    model[linkKey] = !model[linkKey];
    if (model[linkKey]) {
      synchronizing = true;
      model[secondItem.key] = Number(firstController.getValue());
      secondController.setValue(model[secondItem.key]);
      synchronizing = false;
      spriteEditor?.requestUpdate();
    }
    const tooltip = `${model[linkKey] ? 'Unlink' : 'Link'} ${label.toLowerCase()} values`;
    updateSpriteLinkButton(linkController, model[linkKey], tooltip);
  } };
  const linkController = firstController.append(linkAction, 'toggleSpriteLink');
  decorate(linkController, {
    label: '', icon: 'link', iconOnly: true,
    tooltip: `Unlink ${label.toLowerCase()} values`,
    className: 'deckmaker-sprite-link',
  });
  finishRow(firstController, 'deckmaker-number-row deckmaker-sprite-compact-row deckmaker-sprite-linked-row');
  const row = firstController.$multi || firstController.domElement;
  const rowLabel = document.createElement('span');
  rowLabel.className = 'deckmaker-row-label';
  rowLabel.textContent = label;
  row.prepend(rowLabel);
  return { firstController, secondController, linkController };
}

function updateSpritePresetControls() {
  const value = String(model.spriteSizing || 'Grid');
  if (value === 'Grid') model.spriteMode = 'Grid';
  else if (value === 'Custom') model.spriteMode = 'Custom';
  else {
    model.spriteMode = 'Preset';
    model.spritePreset = value;
  }
  if (spriteCustomRow) spriteCustomRow.hidden = model.spriteMode !== 'Custom';
  spriteEditor?.requestUpdate();
}

function buildSpritesheetGui(options) {
  const container = elements.spritesheetGui;
  container.replaceChildren();
  spriteGui = new XGUI({
    title: 'Spritesheet',
    container,
    autoPlace: false,
    width: 344,
  });
  spriteGui.domElement.classList.add('deckmaker-menu', 'deckmaker-spritesheet-menu');
  const closeButton = titleButton(
    spriteGui.$multi || spriteGui.$title,
    'deckmaker-spritesheet-close',
    '\u00d7',
    'Close spritesheet mode',
    leaveSpritesheet,
  );
  closeButton.setAttribute('aria-label', 'Close spritesheet mode');
  spriteGui.$title.after(elements.pdfPageControls);

  settingRow(spriteGui, [
    { key: 'spriteCols', args: [1, 30, 1], label: '', icon: 'view-column', tooltip: 'Number of columns (1–30)', iconOnly: true, local: true, live: true },
    { key: 'spriteRows', args: [1, 30, 1], label: '', icon: 'view-stream', tooltip: 'Number of rows (1–30)', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row');

  linkedSpriteRow('Gaps',
    { key: 'spriteGapH', args: [0, 30, .1], label: '', icon: 'horizontal-distribute', tooltip: 'Horizontal gap in millimetres', iconOnly: true },
    { key: 'spriteGapV', args: [0, 30, .1], label: '', icon: 'vertical-distribute', tooltip: 'Vertical gap in millimetres', iconOnly: true },
    'spriteLinkGaps');
  linkedSpriteRow('Left / right',
    { key: 'spriteLeft', args: [0, 30, .1], label: '', icon: 'align-horizontal-left', tooltip: 'Left border in millimetres', iconOnly: true },
    { key: 'spriteRight', args: [0, 30, .1], label: '', icon: 'align-horizontal-right', tooltip: 'Right border in millimetres', iconOnly: true },
    'spriteLinkHorizontalBorders');
  linkedSpriteRow('Top / bottom',
    { key: 'spriteTop', args: [0, 30, .1], label: '', icon: 'vertical-align-top', tooltip: 'Top border in millimetres', iconOnly: true },
    { key: 'spriteBottom', args: [0, 30, .1], label: '', icon: 'vertical-align-bottom', tooltip: 'Bottom border in millimetres', iconOnly: true },
    'spriteLinkVerticalBorders');

  const presetNames = ['Grid', ...Object.keys(options.cardPresets || {}), 'Custom'];
  settingRow(spriteGui, [{
    key: 'spriteSizing', args: [presetNames], label: 'Preset', icon: 'playing-cards',
    tooltip: 'Use the grid, a known frame preset, or a custom frame size',
    onChange: updateSpritePresetControls,
  }]);
  const customController = labeledSettingRow(spriteGui, 'Custom', [
    { key: 'spriteWidth', args: [1, 1000, .1], label: '', icon: 'width', tooltip: 'Custom frame width in millimetres', iconOnly: true, local: true, live: true },
    { key: 'spriteHeight', args: [1, 1000, .1], label: '', icon: 'height', tooltip: 'Custom frame height in millimetres', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row deckmaker-sprite-custom-row');
  spriteCustomRow = customController?.$multi || customController?.domElement;

  const copyAction = { spriteCopyDefinition: () => spriteEditor?.copyDefinition() };
  spriteCopyDefinitionController = spriteGui.add(copyAction, 'spriteCopyDefinition');
  decorate(spriteCopyDefinitionController, {
    label: 'Copy definition SP1', icon: 'content-copy',
    tooltip: 'Copy the complete spritesheet definition ready to paste into the dataset',
    className: 'deckmaker-sprite-copy-definition',
  });
  finishRow(spriteCopyDefinitionController, 'deckmaker-primary-actions');
  updateSpritePresetControls();
  spriteGui.open();
  container.hidden = true;
}

function enterSpritesheet() {
  if (!spriteEditor?.active) return;
  spriteSequence += 1;
  model.spriteAlias = `SP${spriteSequence}`;
  spriteEditor.requestUpdate();
  if (spriteCopyDefinitionController) {
    spriteCopyDefinitionController.name(`Copy definition SP${spriteSequence}`);
    spriteCopyDefinitionController.icon(materialIcon('content-copy'));
  }
  spriteGui?.open();
  elements.spritesheetGui.hidden = false;
  elements.workspace.classList.add('spritesheet-mode');
  if (!spriteLayoutBefore) {
    spriteLayoutBefore = {
      dock: datasetDock,
      previewHeight: elements.previewViewport.closest('.preview-panel')?.getBoundingClientRect().height || 0,
      datasetWidth: elements.sheetEditor.getBoundingClientRect().width || 0,
    };
  }
  requestAnimationFrame(() => {
    const menuHeight = Math.max(
      gui?.domElement.scrollHeight || 0,
      spriteGui?.domElement.scrollHeight || 0,
    );
    const requiredHeight = Math.min(window.innerHeight * .9, menuHeight + 8);
    if (datasetDock === 'bottom' && requiredHeight > spriteLayoutBefore.previewHeight) {
      setDeckMakerPaneHeight(requiredHeight, false);
    } else if (datasetDock === 'right') {
      const requiredPreviewWidth = 275 + 344 + 12;
      const available = elements.previewViewport.closest('.preview-panel')?.getBoundingClientRect().width || 0;
      if (available < requiredPreviewWidth) {
        setDeckMakerPaneWidth(Math.max(240, window.innerWidth - requiredPreviewWidth - 8), false);
      }
    }
    previewViewer.fit();
  });
}

function actionRow(folder, items, className = '') {
  let first;
  for (const item of items) {
    const holder = { [item.key]: item.run || (() => command(item.command)) };
    const controller = first ? first.append(holder, item.key) : folder.add(holder, item.key);
    decorate(controller, item);
    actionControllers.push({ controller, command: item.command || '' });
    first ||= controller;
  }
  finishRow(first, className);
  return first;
}

function mainActionRow(folder) {
  actionRow(folder, [
    {
      key: 'generateAction', command: 'generate', label: 'Create all',
      image: '/shared-assets/deckmaker_icon.png',
      tooltip: 'Refresh the dataset and create every item in the editable output SVG.',
      className: 'deckmaker-action-button',
    },
    {
      key: 'openAction', command: 'open-output', label: 'Open', icon: 'simple-icons:inkscape',
      tooltip: 'Open the last generated output SVG in Inkscape.',
      className: 'deckmaker-action-button',
    },
    {
      key: 'exportAction', command: 'export', label: 'Export', icon: 'file-export',
      tooltip: 'Export the last generated SVG using the formats and selection configured in Export options.',
      className: 'deckmaker-action-button',
    },
    {
      key: 'playCreateAction', command: 'play-create', label: 'Build assets', icon: 'deployed-code-update',
      tooltip: 'Create or update the PnPPlay asset pack in every enabled asset store.',
      className: 'deckmaker-action-button',
    },
  ], 'deckmaker-main-actions');
}

function buildDatasetGui(state, options) {
  datasetGui = gui.addFolder(datasetTitle(state));
  datasetGui.icon(materialIcon('dataset'));
  datasetGui.domElement.classList.add('deckmaker-dataset-folder');
  [...datasetGui.$title.childNodes]
    .filter(node => node.nodeType === Node.TEXT_NODE)
    .forEach(node => node.remove());
  datasetTitleLabel = document.createElement('span');
  datasetTitleLabel.className = 'deckmaker-folder-title-label';
  datasetTitleLabel.textContent = datasetTitle(state);
  datasetGui.$title.append(datasetTitleLabel);

  datasetDockController = titleButton(
    datasetGui.$multi,
    'deckmaker-dataset-dock',
    '',
    'Move dataset to the right',
    () => setDatasetDock(datasetDock === 'bottom' ? 'right' : 'bottom'),
  );

  const sourceController = settingRow(datasetGui, [
    {
      key: 'sourceMode', args: [options.sourceModes], label: 'Source', icon: 'database',
      tooltip: 'Select the dataset source used by this template.',
    },
  ], 'deckmaker-dataset-source-row');
  const googleSheetToCsvAction = { googleSheetToCsvAction: () => command('gsheet-to-csv') };
  googleSheetToCsvController = sourceController.append(googleSheetToCsvAction, 'googleSheetToCsvAction');
  decorate(googleSheetToCsvController, {
    label: 'GS->CSV',
    icon: 'cloud-download',
    tooltip: 'Copy the current Google Sheet to the local CSV and switch to it. The existing CSV will be replaced and preserved as .csv.old for emergency manual recovery.',
    className: 'deckmaker-gsheet-to-csv',
  });
  settingRow(datasetGui, [
    { key: 'sheetId', label: '', icon: 'dataset', tooltip: 'Google Sheets spreadsheet ID', iconOnly: true, inputType: 'text', attributes: { placeholder: 'Spreadsheet ID', autocomplete: 'off', spellcheck: 'false' } },
    { key: 'sheetRange', label: '', icon: 'table-rows', tooltip: 'Sheet range, title or gid', iconOnly: true, inputType: 'text', attributes: { placeholder: 'Range or gid', autocomplete: 'off', spellcheck: 'false' } },
  ], 'deckmaker-fields');
  const openSheetAction = { openGoogleSheetAction: openGoogleSheet };
  openSheetController = datasetGui.add(openSheetAction, 'openGoogleSheetAction');
  decorate(openSheetController, {
    label: 'Open GSheet',
    icon: 'open-in-new',
    tooltip: 'Open the linked spreadsheet directly in browser',
    className: 'deckmaker-dataset-open',
  });
  finishRow(openSheetController, 'deckmaker-dataset-actions');
  updateDatasetDockButton();
  actionControllers.push({ controller: openSheetController, command: 'open-google-sheet' });
  actionControllers.push({ controller: googleSheetToCsvController, command: 'gsheet-to-csv' });
  datasetGui.close();
}

function buildPreviewGui(state, options) {
  const preview = gui.addFolder('Preview');
  preview.icon(materialIcon('preview'));
  preview.domElement.classList.add('deckmaker-preview-folder');
  previewAssetSelect = document.createElement('select');
  previewAssetSelect.className = 'deckmaker-preview-asset-select';
  previewAssetSelect.title = 'Preview the selected asset from the synchronized dataset';
  previewAssetSelect.addEventListener('click', event => event.stopPropagation());
  previewAssetSelect.addEventListener('pointerdown', event => event.stopPropagation());
  previewAssetSelect.addEventListener('change', () => {
    leaveSpritesheet();
    save('previewAsset', previewAssetSelect.value);
  });
  preview.$multi.append(previewAssetSelect);
  previewPrevious = titleButton(preview.$multi, 'deckmaker-preview-previous', '<', 'Previous cached asset', () => {
    leaveSpritesheet();
    command('preview-previous');
  });
  previewNext = titleButton(preview.$multi, 'deckmaker-preview-next', '>', 'Next cached asset', () => {
    leaveSpritesheet();
    command('preview-next');
  });
  const viewerController = settingRow(preview, [
    {
      key: 'previewBackend', args: [options.previewBackends], label: 'Viewer', icon: 'visibility',
      tooltip: 'Browser renders 96/600 DPI PNGs. Inkscape opens the generated SVG in an isolated persistent viewer.',
    },
  ]);
  templateRefresh = titleButton(
    viewerController.$multi || viewerController.domElement,
    'deckmaker-template-refresh',
    '',
    'Reload the current template and rebuild all dataset previews',
    () => {
      leaveSpritesheet();
      command('preview-refresh');
    },
  );
  templateRefresh.append(gui._createIcon(materialIcon('refresh')));
  settingRow(preview, [
    {
      key: 'previewSelectionSync', args: [PREVIEW_SYNC_MODES], label: 'Selection sync', icon: 'sync-alt',
      tooltip: 'Keep the selected dataset cell and preview asset synchronized. Two-way also follows selections made in the Inkscape viewer.',
      onChange: value => {
        model.previewSelectionSync = Object.values(PREVIEW_SYNC_MODES).includes(value) ? value : 'off';
        save('previewSelectionSync', model.previewSelectionSync);
        lastPreviewSelection = '';
      },
    },
  ], 'deckmaker-preview-sync-row');
  updatePreviewNavigation(state);
  preview.close();
  return preview;
}

function build(state) {
  if (gui) {
    built = true;
    return;
  }
  built = true;
  const options = state.options;
  const container = document.querySelector('#gui');
  container.replaceChildren();
  gui = new XGUI({
    title: menuTitle(state),
    container,
    autoPlace: false,
    width: 275,
  });
  gui.domElement.classList.add('deckmaker-menu');
  gui.icon(materialIcon('view-quilt'));

  if (ENABLE_SPRITESHEETS) {
    spriteEditor = new SpritesheetEditor({
      image: elements.previewImage,
      overlay: elements.spriteGridOverlay,
      pdfControls: elements.pdfPageControls,
      pdfPrevious: elements.pdfPagePrevious,
      pdfNext: elements.pdfPageNext,
      pdfLabel: elements.pdfPageLabel,
    }, model, options.cardPresets, previewViewer, toast, api, url => {
      const separator = String(url || '').includes('?') ? '&' : '?';
      return `${url}${separator}token=${encodeURIComponent(token)}`;
    }, () => {
      controllers.forEach(controller => controller.updateDisplay());
      if (spriteGui?.$title) spriteGui.$title.title = spriteEditor.sourceDescription();
    });
    buildSpritesheetGui(options);
  }

  buildDatasetGui(state, options);
  buildPreviewGui(state, options);
  updateMenuIdentity(state);

  toolsFolder = gui.addFolder('Tools');
  toolsFolder.icon(materialIcon('construction'));
  mainActionRow(toolsFolder);
  actionRow(toolsFolder, [
    {
      key: 'copyTemplateHeadersAction', label: 'Copy template headers', icon: 'content-copy',
      tooltip: 'Copy the template bbox and its element names as a tab-separated row ready to paste into the dataset.',
      run: copyTemplateHeaders,
    },
  ]);

  const configuration = gui.addFolder('Configuration');
  configuration.icon(materialIcon('settings'));

  const exportFolder = configuration.addFolder('Export options');
  exportFolder.icon(materialIcon('file-export'));
  const play = configuration.addFolder('PnPPlay');
  play.icon(materialIcon('sports-esports'));
  const advanced = configuration.addFolder('Advanced');
  advanced.icon(materialIcon('settings'));
  settingRow(exportFolder, [
    { key: 'exportPdf', label: 'PDF', icon: 'picture-as-pdf', tooltip: 'Export a standard PDF', live: true },
    { key: 'exportPdfx', label: 'PDF/X', icon: 'verified', tooltip: 'Export print-ready PDF/X', live: true },
    { key: 'exportOther', label: 'Image', icon: 'image', tooltip: 'Export PNG, JPEG, WebP or another format', live: true },
  ], 'deckmaker-output-row');
  settingRow(exportFolder, [
    { key: 'otherFormat', args: [options.formats], label: '', icon: 'photo-library', tooltip: 'Raster/vector output format', iconOnly: true },
    { key: 'otherUnit', args: [options.units], label: '', icon: 'select-all', tooltip: 'Export pages, generated items or SVG IDs', iconOnly: true },
    { key: 'otherSelection', label: '', icon: 'filter-alt', tooltip: 'Pages, items or IDs to export', iconOnly: true, inputType: 'text', attributes: { placeholder: 'Selection', autocomplete: 'off', spellcheck: 'false' } },
  ], 'deckmaker-fields');
  settingRow(exportFolder, [
    { key: 'exportDpi', args: [1, 2400, 1], label: 'DPI', icon: 'high-quality', tooltip: 'Raster export resolution in dots per inch' },
    { key: 'jpegQuality', args: [70, 95, 1], label: 'Quality', icon: 'center-focus-strong', tooltip: 'JPEG compression quality' },
  ], 'deckmaker-number-row');
  settingRow(exportFolder, [
    { key: 'cutTemplate', label: 'Cut', icon: 'content-cut', tooltip: 'Generate a cutting template', live: true },
    { key: 'cutFormat', args: [options.cutFormats], label: 'Format', icon: 'polyline', tooltip: 'Cutting template format' },
  ]);

  const pdf = exportFolder.addFolder('PDF options');
  pdf.icon(materialIcon('picture-as-pdf'));
  settingRow(pdf, [
    { key: 'pdfDefault', label: '', icon: 'draft', tooltip: 'Default PDF profile', iconOnly: true, live: true },
    { key: 'pdfScreen', label: '', icon: 'monitor', tooltip: 'Screen PDF profile', iconOnly: true, live: true },
    { key: 'pdfEbook', label: '', icon: 'tablet', tooltip: 'E-book PDF profile', iconOnly: true, live: true },
    { key: 'pdfPrinter', label: '', icon: 'print', tooltip: 'Printer PDF profile', iconOnly: true, live: true },
    { key: 'pdfPrepress', label: '', icon: 'precision-manufacturing', tooltip: 'Prepress PDF profile', iconOnly: true, live: true },
  ], 'deckmaker-profile-row');
  settingRow(pdf, [
    { key: 'pdfRasterMode', args: [options.rasterModes], label: 'Filters', icon: 'blur-on', tooltip: 'How SVG filters are exported to PDF' },
    { key: 'pdfxVersion', args: [options.pdfxVersions], label: 'PDF/X', icon: 'verified', tooltip: 'PDF/X compatibility version' },
  ]);
  settingRow(pdf, [
    { key: 'pdfCmykIcc', args: [options.iccProfiles], label: 'ICC', icon: 'palette', tooltip: 'CMYK ICC output profile' },
    { key: 'pdfPureBlack', label: 'Black', icon: 'format-color-fill', tooltip: 'Keep black text as pure K', live: true },
  ]);

  const parts = exportFolder.addFolder('SVG parts');
  parts.icon(materialIcon('call-split'));
  settingRow(parts, [
    { key: 'splitSvg', label: 'Split', icon: 'call-split', tooltip: 'Split large SVG output into parts', live: true },
    { key: 'splitMode', args: [['parts', 'limits']], label: 'By', icon: 'tune', tooltip: 'Split by a fixed count or by limits' },
  ]);
  settingRow(parts, [
    { key: 'splitParts', args: [0, 10000, 1], label: 'Parts', icon: 'file-copy', tooltip: 'Number of output SVG parts' },
    { key: 'splitPages', args: [0, 1000000, 1], label: 'Pages', icon: 'menu-book', tooltip: 'Maximum pages per SVG part' },
  ], 'deckmaker-number-row');
  settingRow(parts, [
    { key: 'splitRecords', args: [0, 100000000, 1], label: 'Items', icon: 'style', tooltip: 'Maximum generated items per SVG part' },
    { key: 'splitMb', args: [0, 2048, 1], label: 'MB', icon: 'database', tooltip: 'Maximum approximate file size per SVG part' },
  ], 'deckmaker-number-row');
  pdf.close();
  parts.close();

  settingRow(advanced, [
    { key: 'inkscapeWorkers', args: [1, 32, 1], label: 'Workers', icon: 'speed', tooltip: 'Parallel Inkscape export workers' },
    { key: 'templateEngine', args: [options.templateEngines], label: 'Engine', icon: 'manufacturing', tooltip: 'Template rendering engine' },
  ]);
  settingRow(advanced, [
    { key: 'consoleLog', args: [options.logLevels], label: 'Console', icon: 'terminal', tooltip: 'Console logging level' },
    { key: 'fileLog', args: [options.logLevels], label: 'File', icon: 'draft', tooltip: 'File logging level' },
  ]);
  settingRow(advanced, [
    { key: 'imagePreflight', label: 'DPI report', icon: 'image-search', tooltip: 'Create an image DPI preflight report', live: true },
  ]);
  actionRow(advanced, [
    { key: 'preferencesAction', command: 'open-preferences', label: 'Preferences', icon: 'tune', tooltip: 'Open PnPInk preferences' },
  ]);

  actionRow(play, [
    { key: 'playServerAction', command: 'play-server', label: 'Server', icon: 'power-settings-new', tooltip: 'Start or stop the local PnPPlay server' },
    { key: 'playDesignerAction', command: 'play-designer', label: 'Designer', icon: 'palette', tooltip: 'Open the local PnPPlay Game Designer' },
  ], 'deckmaker-play-actions');
  playServerController = actionControllers.find(entry => entry.command === 'play-server')?.controller;
  const stores = play.addFolder('Stores');
  const storeModel = { local: true, s3: false, r2: false };
  const localStore = decorate(stores.add(storeModel, 'local'), { label: 'Local', icon: 'hard-drive', tooltip: 'Publish assets to the local content-addressed store' });
  localStore.disable();
  const s3Store = decorate(localStore.append(storeModel, 's3'), { label: 'S3', icon: 'cloud', tooltip: 'S3 stores are not configured yet' });
  s3Store.disable();
  const r2Store = decorate(localStore.append(storeModel, 'r2'), { label: 'R2', icon: 'cloud-sync', tooltip: 'Cloudflare R2 stores are not configured yet' });
  r2Store.disable();
  finishRow(localStore, 'deckmaker-store-row');
  stores.close();

  const help = gui.addFolder('Help');
  help.icon(materialIcon('help'));
  actionRow(help, [
    { key: 'introAction', command: 'open-intro', label: '', icon: 'info', tooltip: 'PnPInk introduction', iconOnly: true },
    { key: 'guideAction', command: 'open-guide', label: '', icon: 'menu-book', tooltip: 'Open PnPInk guide', iconOnly: true },
    { key: 'examplesAction', command: 'open-examples', label: '', icon: 'folder-special', tooltip: 'Open bundled examples', iconOnly: true },
  ], 'deckmaker-icon-row');
  actionRow(help, [
    { key: 'liveLogAction', label: 'Live log', icon: 'terminal', tooltip: 'Show or hide the live activity log', run: toggleLog },
    { key: 'logAction', command: 'open-log', label: 'Log file', icon: 'draft', tooltip: 'Open the complete log file' },
    { key: 'clearLogAction', label: 'Clear', icon: 'delete', tooltip: 'Clear the visible activity log', run: () => { elements.log.textContent = ''; } },
  ]);
  actionRow(help, [
    { key: 'installAppAction', label: 'Install', icon: 'install-desktop', tooltip: 'Install Deckmaker as an app', run: () => appShell.install() },
    { key: 'fullscreenAction', label: 'Full screen', icon: 'fullscreen', tooltip: 'Enter or leave full screen', run: () => appShell.toggleFullscreen() },
  ], 'deckmaker-app-actions');
  const helpControllers = help.controllersRecursive();
  installController = helpControllers.find(controller => controller.property === 'installAppAction');
  fullscreenController = helpControllers.find(controller => controller.property === 'fullscreenAction');
  appShell.subscribe(shell => {
    installController?._show(shell.canInstall);
    fullscreenController?.name(shell.fullscreen ? 'Exit full screen' : 'Full screen');
  });
  [exportFolder, toolsFolder, play, advanced, configuration, help].filter(Boolean).forEach(folder => folder.close());
  gui.close();
}

function applyState(state) {
  latestState = state;
  sequence = Math.max(sequence, Number(state.seq || 0));
  Object.assign(model, state.preferences || {}, {
    sourceMode: state.sourceMode || '',
    sheetId: state.sheetId || '',
    sheetRange: state.sheetRange || '',
  });
  if (!spriteEditor?.active) model.spriteSource = state.spritesheet?.available
    ? `${state.spritesheet.nodeId} · ${Number(state.spritesheet.widthMm).toFixed(1)} × ${Number(state.spritesheet.heightMm).toFixed(1)} mm`
    : 'Drop an image on the canvas';
  if (!built) build(state);
  updateMenuIdentity(state);
  const datasetEditor = state.datasetEditor || {};
  const editorKind = String(datasetEditor.kind || '');
  const sheetId = String(state.sheetId || '').trim();
  const sheetGid = String(state.sheetGid ?? '').trim();
  const sheetUrl = sheetEditorUrl(state);
  const sheetKey = `${sheetId}:${sheetGid}:${sheetViewMode}`;
  if (editorKind === 'gsheet' && elements.sheetEditorFrame.dataset.sheetKey !== sheetKey) {
    elements.sheetEditorFrame.dataset.sheetKey = sheetKey;
    elements.sheetEditorFrame.src = sheetUrl || 'about:blank';
  }
  initializeSplitter(Boolean(editorKind));
  if (!spriteEditor?.active) previewViewer.setPreview(state.preview);
  elements.sheetEditor.hidden = false;
  elements.sheetEditor.classList.toggle('empty', !editorKind);
  elements.sheetEditorFrame.hidden = editorKind !== 'gsheet';
  if (editorKind === 'csv') {
    datasetTable.show(datasetEditor);
  } else {
    datasetTable.hide();
    elements.sheetEditorMessage.hidden = Boolean(editorKind);
    const sheetStatus = String(state.sheetStatus || '');
    if (!editorKind) {
      showMissingDatasetGuide();
    } else {
      elements.sheetEditorMessage.textContent = sheetStatus === 'error'
        ? 'Could not resolve the Google Sheets tab. Check access or select a numeric gid.'
        : 'Resolving sheet tab...';
    }
  }
  spriteEditor?.setPresets(state.options?.cardPresets || {});
  spriteEditor?.setMetadata(state.spritesheet);
  controllers.forEach(controller => controller.updateDisplay());
  const availability = {
    generate: Boolean(state.canGenerate),
    preview: Boolean(state.canGenerate),
    'open-output': Boolean(state.canOpenOutput),
    export: Boolean(state.canExport),
    'play-create': Boolean(state.canGenerate),
    'play-server': Boolean(state.template),
    'play-designer': Boolean(state.template),
    'open-google-sheet': Boolean(state.sheetId),
    'gsheet-to-csv': Boolean(state.sheetId) && state.sourceMode !== 'local_csv',
    'create-starter-csv': Boolean(state.canCreateDataset),
  };
  actionControllers.forEach(({ controller, command: action }) => {
    const available = !(action in availability) || availability[action];
    if (state.busy || !available) controller.disable();
    else controller.enable();
  });
  if (playServerController) {
    const running = Boolean(state.play?.serverRunning);
    playServerController.name(running ? 'Stop' : 'Server');
    playServerController.domElement.title = running
      ? `Stop local PnPPlay server (port ${state.play?.serverPort || ''})`
      : 'Start local PnPPlay server';
    playServerController.domElement.classList.toggle('pnpplay-server-running', running);
  }
  const progress = state.progress || {};
  const current = Number(progress.current || 0);
  const total = Number(progress.total || 0);
  elements.jobTitle.textContent = state.status || 'Ready';
  elements.jobActivity.textContent = state.activity || progress.label || '';
  const authorizationUrl = String(state.googleAuthorizationUrl || '').trim();
  elements.googleAuthLink.hidden = !authorizationUrl;
  if (authorizationUrl) elements.googleAuthLink.href = authorizationUrl;
  const failed = /^Error:/i.test(state.status || '');
  const busy = Boolean(state.busy);
  const previewBusy = busy && state.job === 'preview';
  if (busy && !wasBusy && !['preview', 'gsheet-to-csv'].includes(state.job)) logDisplayMode = 'auto';
  updateLogVisibility(busy);
  wasBusy = busy;
  elements.splitter.classList.toggle('preview-busy', previewBusy);
  elements.splitter.setAttribute('aria-busy', previewBusy ? 'true' : 'false');
  elements.jobStatus.classList.toggle('active', (!previewBusy && busy) || failed);
  elements.jobStatus.classList.toggle('error', failed);
  elements.jobSpinner.hidden = !state.busy;
  if (failed && state.status !== lastStatus) toast(state.status);
  lastStatus = state.status || '';
  if (total > 0) {
    elements.jobProgress.hidden = false;
    elements.jobProgress.max = total;
    elements.jobProgress.value = Math.min(current, total);
  } else {
    elements.jobProgress.hidden = true;
    elements.jobProgress.value = 0;
  }
  if (Array.isArray(state.logs)) {
    const followsTail = !elements.log.textContent
      || elements.log.scrollHeight - elements.log.scrollTop - elements.log.clientHeight < 24;
    elements.log.textContent = state.logs.join('\n');
    if (followsTail) elements.log.scrollTop = elements.log.scrollHeight;
  }
}

function appendEventLogs(events) {
  const lines = (events || []).flatMap(event => {
    if (event?.kind === 'log' && event.text) return [String(event.text)];
    if (event?.kind === 'logs' && Array.isArray(event.lines)) return event.lines.map(String);
    return [];
  });
  if (!lines.length) return;
  const followsTail = !elements.log.textContent
    || elements.log.scrollHeight - elements.log.scrollTop - elements.log.clientHeight < 24;
  const current = elements.log.textContent ? elements.log.textContent.split('\n') : [];
  elements.log.textContent = current.concat(lines).slice(-500).join('\n');
  if (followsTail) elements.log.scrollTop = elements.log.scrollHeight;
}

function handleHostEvents(events) {
  if (!(events || []).some(event => event?.kind === 'activate')) return;
  window.focus();
  document.documentElement.focus?.({ preventScroll: true });
}

async function poll() {
  while (true) {
    try {
      const data = await api(`/api/events?after=${sequence}&timeout=5`);
      sequence = Math.max(sequence, Number(data.seq || 0));
      appendEventLogs(data.events);
      handleHostEvents(data.events);
      if (data.state) applyState(data.state);
    } catch (error) {
      toast(error.message);
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
}

let dropDepth = 0;

function spriteFileFromTransfer(dataTransfer) {
  return Array.from(dataTransfer?.files || []).find(file => (
    String(file.type || '').startsWith('image/')
    || String(file.type || '').toLowerCase() === 'application/pdf'
    || /\.(?:png|jpe?g|webp|gif|svg|bmp|avif|pdf)$/i.test(String(file.name || ''))
  )) || null;
}

function isCanvasImageDrag(event) {
  return elements.previewViewport.contains(event.target)
    && (spriteFileFromTransfer(event.dataTransfer) || Array.from(event.dataTransfer?.types || []).includes('Files'));
}

function clearDropFeedback() {
  document.body.classList.remove('url-drop-active', 'image-drop-active');
  datasetGui?.domElement.classList.remove('deckmaker-drop-target');
}

window.addEventListener('dragenter', event => {
  event.preventDefault();
  dropDepth += 1;
  if (isCanvasImageDrag(event)) document.body.classList.add('image-drop-active');
  else {
    document.body.classList.add('url-drop-active');
    datasetGui?.domElement.classList.add('deckmaker-drop-target');
  }
});

window.addEventListener('dragover', event => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = isCanvasImageDrag(event) ? 'copy' : 'link';
});

window.addEventListener('dragleave', () => {
  dropDepth = Math.max(0, dropDepth - 1);
  if (!dropDepth) clearDropFeedback();
});

window.addEventListener('drop', async event => {
  event.preventDefault();
  dropDepth = 0;
  clearDropFeedback();
  const image = elements.previewViewport.contains(event.target)
    ? spriteFileFromTransfer(event.dataTransfer)
    : null;
  if (image) {
    if (await spriteEditor?.loadFile(image)) {
      enterSpritesheet();
      toast(`Spritesheet source loaded: ${image.name}`);
    }
    return;
  }
  const sheet = googleSheetFromDataTransfer(event.dataTransfer);
  if (!sheet) return;
  Object.assign(model, {
    sourceMode: 'auto',
    sheetId: sheet.sheetId,
    sheetRange: sheet.gid || '',
  });
  openSheetController?.enable();
  controllers.forEach(controller => controller.updateDisplay());
  if (datasetGui?.$title) datasetGui.$title.title = sheet.url;
  try {
    applyState(await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ sourceMode: 'auto', sheetId: sheet.sheetId, sheetRange: sheet.gid || '' }),
    }));
    toast(`Google Sheet linked${sheet.gid ? ` (gid ${sheet.gid})` : ''}. Press Generate to use it.`);
  } catch (error) {
    toast(error.message);
  }
});

try {
  const browserSessionState = await openBrowserSession();
  restoreWindowGeometry(browserSessionState.geometry);
  const initialState = await api('/api/state');
  applyState(initialState);
  const canAutoPreview = Boolean(
    initialState.canGenerate
    && initialState.template
    && initialState.datasetDisplay
    && !initialState.busy
    && !initialState.preview?.available
  );
  if (canAutoPreview) await command('preview-initial');
  poll();
} catch (error) {
  toast(error.message);
}
