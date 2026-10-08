import { XGUI } from './vendor/lil-xgui.min.js';
import { googleSheetFromDataTransfer } from './gsheet-drop.js';
import { SpritesheetEditor } from './spritesheet-editor.js';
import { appShell } from './app-shell.js';
import { PreviewViewer } from './preview-viewer.js';

const materialIcon = name => `material-symbols-light:${name}`;
const ENABLE_SPRITESHEETS = true;

const query = new URLSearchParams(location.search);
const token = query.get('token') || sessionStorage.getItem('deckmaker-token') || '';
if (token) sessionStorage.setItem('deckmaker-token', token);
const browserSession = globalThis.crypto?.randomUUID?.()
  || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

const elements = {
  log: document.querySelector('#log'),
  toast: document.querySelector('#toast'),
  consolePanel: document.querySelector('#console-panel'),
  sheetEditor: document.querySelector('#gsheet-editor'),
  sheetEditorFrame: document.querySelector('#gsheet-editor-frame'),
  sheetEditorMessage: document.querySelector('#gsheet-editor-message'),
  previewViewport: document.querySelector('#preview-viewport'),
  previewStage: document.querySelector('#preview-stage'),
  previewImage: document.querySelector('#preview-image'),
  previewMessage: document.querySelector('#preview-message'),
  previewScale: document.querySelector('#preview-scale'),
  spriteGridOverlay: document.querySelector('#sprite-grid-overlay'),
  workspace: document.querySelector('main'),
  splitter: document.querySelector('#workspace-splitter'),
  logClose: document.querySelector('#log-close'),
  jobStatus: document.querySelector('#job-status'),
  jobSpinner: document.querySelector('#job-spinner'),
  jobTitle: document.querySelector('#job-title'),
  jobActivity: document.querySelector('#job-activity'),
  googleAuthLink: document.querySelector('#google-auth-link'),
  jobProgress: document.querySelector('#job-progress'),
};

const model = {
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
  spriteDefinition: 'Drop an image on the canvas',
  spriteFrame: '@sprites[*]',
};
const previewViewer = new PreviewViewer({
  viewport: elements.previewViewport,
  stage: elements.previewStage,
  image: elements.previewImage,
  message: elements.previewMessage,
  scale: elements.previewScale,
  overlays: [elements.spriteGridOverlay],
}, token);
const controllers = [];
const actionControllers = [];
let sequence = 0;
let built = false;
let spriteEditor;
let gui;
let datasetFolder;
let toolsFolder;
let spritesFolder;
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
let sheetEditorActive = false;
let sheetActivitySentAt = 0;
let sheetActivityChain = Promise.resolve();
let sheetViewMode = 'minimal';

function leaveSpritesheet() {
  spriteEditor?.deactivate();
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
  elements.toast.textContent = message;
  elements.toast.classList.add('visible');
  setTimeout(() => elements.toast.classList.remove('visible'), 3200);
}

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
function closeBrowserSession() {
  const params = new URLSearchParams({ token, session: browserSession });
  if (navigator.sendBeacon?.(`/api/session/close?${params}`, new Blob(['{}'], { type: 'application/json' }))) return;
  fetch('/api/session/close', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-DeckMaker-Token': token,
      'X-DeckMaker-Session': browserSession,
    },
    body: '{}',
    keepalive: true,
  }).catch(() => {});
}

window.addEventListener('pagehide', closeBrowserSession);

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
window.addEventListener('pageshow', () => openBrowserSession().catch(() => {}));
setInterval(() => reportBrowserVisibility().catch(() => {}), 5000);

function updateLogVisibility(busy = Boolean(latestState.busy)) {
  const previewJob = String(latestState.job || '') === 'preview';
  const visible = logDisplayMode === 'open' || (logDisplayMode === 'auto' && busy && !previewJob);
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
  if (remember) localStorage.setItem('deckmaker-pane-height', String(height));
  updateSheetViewMode(height <= window.innerHeight * .15 ? 'demo' : 'minimal');
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
  if (!sheetId) return;
  const sheetGid = String(latestState.sheetGid ?? '').trim();
  const sheetKey = `${sheetId}:${sheetGid}:${sheetViewMode}`;
  elements.sheetEditorFrame.dataset.sheetKey = sheetKey;
  elements.sheetEditorFrame.src = sheetEditorUrl();
}

function initializeSplitter(hasSheet) {
  if (splitterReady) {
    if (!sheetConfigured && hasSheet) {
      const remembered = Number(localStorage.getItem('deckmaker-pane-height'));
      setDeckMakerPaneHeight(remembered || Math.min(320, window.innerHeight * .42), false);
    }
    sheetConfigured = hasSheet;
    return;
  }
  splitterReady = true;
  sheetConfigured = hasSheet;
  const remembered = Number(localStorage.getItem('deckmaker-pane-height'));
  const initial = hasSheet
    ? (remembered || Math.min(320, window.innerHeight * .42))
    : window.innerHeight - 145;
  setDeckMakerPaneHeight(initial, false);
}

elements.splitter.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  event.preventDefault();
  elements.splitter.setPointerCapture?.(event.pointerId);
  const move = moveEvent => setDeckMakerPaneHeight(moveEvent.clientY - elements.workspace.getBoundingClientRect().top);
  const finish = () => {
    elements.splitter.removeEventListener('pointermove', move);
    elements.splitter.removeEventListener('pointerup', finish);
    elements.splitter.removeEventListener('pointercancel', finish);
  };
  elements.splitter.addEventListener('pointermove', move);
  elements.splitter.addEventListener('pointerup', finish);
  elements.splitter.addEventListener('pointercancel', finish);
});

elements.splitter.addEventListener('keydown', event => {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
  event.preventDefault();
  const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-pane-height')) || 300;
  setDeckMakerPaneHeight(current + (event.key === 'ArrowDown' ? 16 : -16));
});

elements.splitter.addEventListener('dblclick', event => {
  event.preventDefault();
  const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-pane-height'))
    || window.innerHeight * .5;
  setDeckMakerPaneHeight(window.innerHeight * (current > window.innerHeight * .5 ? .1 : .9));
});

window.addEventListener('resize', () => {
  const current = parseFloat(getComputedStyle(elements.workspace).getPropertyValue('--deckmaker-pane-height'));
  if (current) setDeckMakerPaneHeight(current, false);
});

async function save(key, value) {
  try {
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
  return 'Dataset options';
}

function setGuiTitle(target, title, tooltip = '') {
  if (target?.$title) {
    target._title = title;
    let label = target.$title.querySelector(':scope > .deckmaker-title-text');
    if (!label) {
      [...target.$title.childNodes]
        .filter(node => node.nodeType === Node.TEXT_NODE)
        .forEach(node => node.remove());
      label = document.createElement('span');
      label.className = 'deckmaker-title-text';
      target.$title.append(label);
    }
    label.textContent = title;
    target.$title.title = tooltip;
    if (target.$multi) target.$multi.title = tooltip;
  }
}

function updateMenuIdentity(state) {
  updateTemplateTitle(state);
  const title = `${menuTitle(state)} [${state.templateName || 'No template'}]`;
  setGuiTitle(
    datasetFolder,
    datasetTitle(state),
    `${state.datasetPath || 'No dataset'}\n\nDrag a Google Sheets URL here to link it automatically.`,
  );
  document.title = title;
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
    const titleButton = (className, text, tooltip, handler) => {
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
    };
    templateRefresh = titleButton(
      'deckmaker-template-refresh',
      '',
      'Reload the current template and rebuild all dataset previews',
      () => {
        leaveSpritesheet();
        command('preview-refresh');
      },
    );
    templateRefresh.append(gui._createIcon(materialIcon('refresh')));
    previewPrevious = titleButton('deckmaker-preview-previous', '<', 'Previous cached asset', () => {
      leaveSpritesheet();
      command('preview-previous');
    });
    previewAssetSelect = document.createElement('select');
    previewAssetSelect.className = 'deckmaker-preview-asset-select';
    previewAssetSelect.title = 'Preview the selected asset from the synchronized dataset';
    previewAssetSelect.addEventListener('click', event => event.stopPropagation());
    previewAssetSelect.addEventListener('pointerdown', event => event.stopPropagation());
    previewAssetSelect.addEventListener('change', event => {
      event.stopPropagation();
      leaveSpritesheet();
      save('previewAsset', previewAssetSelect.value);
    });
    titleBar.append(previewAssetSelect);
    previewNext = titleButton('deckmaker-preview-next', '>', 'Next cached asset', () => {
      leaveSpritesheet();
      command('preview-next');
    });
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
  templateRefresh.disabled = !state.canGenerate || Boolean(state.busy);
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
  gui.$title.title = state.template || '';
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
    const callback = item.local ? () => spriteEditor?.requestUpdate() : value => save(item.key, value);
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
  slider.addEventListener('wheel', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    const multiplier = event.shiftKey ? 10 : event.altKey ? .1 : 1;
    const direction = event.deltaY < 0 || event.deltaX > 0 ? 1 : -1;
    const value = Math.max(minimum, Math.min(maximum, Number(controller.getValue()) + direction * step * multiplier));
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
    width: 328,
  });
  gui.domElement.classList.add('deckmaker-menu');
  updateMenuIdentity(state);

  if (ENABLE_SPRITESHEETS) {
    spriteEditor = new SpritesheetEditor({
      image: elements.previewImage,
      overlay: elements.spriteGridOverlay,
    }, model, options.cardPresets, previewViewer, toast, () => {
      controllers.forEach(controller => controller.updateDisplay());
      if (spritesFolder) spritesFolder.$title.title = spriteEditor.sourceDescription();
    });
  }

  mainActionRow(gui);

  toolsFolder = gui.addFolder('Tools');
  toolsFolder.icon(materialIcon('construction'));
  actionRow(toolsFolder, [
    {
      key: 'copyTemplateHeadersAction', label: 'Copy template headers', icon: 'content-copy',
      tooltip: 'Copy the template bbox and its element names as a tab-separated row ready to paste into the dataset.',
      run: copyTemplateHeaders,
    },
  ]);

  const configuration = gui.addFolder('Configuration');
  configuration.icon(materialIcon('settings'));

  datasetFolder = configuration.addFolder(datasetTitle(state));
  datasetFolder.icon(materialIcon('database'));
  const datasetTooltip = 'Dataset source. Drag a Google Sheets URL anywhere onto this window to link it automatically.';
  datasetFolder.$title.title = datasetTooltip;
  datasetFolder.$multi.title = datasetTooltip;
  settingRow(datasetFolder, [
    { key: 'sourceMode', args: [options.sourceModes], label: 'Source', icon: 'database', tooltip: datasetTooltip },
  ]);
  settingRow(datasetFolder, [
    { key: 'sheetId', label: '', icon: 'dataset', tooltip: 'Google Sheets spreadsheet ID', iconOnly: true, inputType: 'text', attributes: { placeholder: 'Spreadsheet ID', autocomplete: 'off', spellcheck: 'false' } },
    { key: 'sheetRange', label: '', icon: 'table-rows', tooltip: 'Sheet range, title or gid', iconOnly: true, inputType: 'text', attributes: { placeholder: 'Range or gid', autocomplete: 'off', spellcheck: 'false' } },
  ], 'deckmaker-fields');
  const openSheetAction = { openGoogleSheetAction: openGoogleSheet };
  openSheetController = datasetFolder.append(openSheetAction, 'openGoogleSheetAction');
  decorate(openSheetController, {
    label: 'Open GSheet',
    icon: 'open-in-new',
    tooltip: 'Open the linked spreadsheet directly in browser',
    className: 'deckmaker-dataset-open',
  });
  actionControllers.push({ controller: openSheetController, command: 'open-google-sheet' });
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

  let sprites;
  if (ENABLE_SPRITESHEETS) {
    sprites = toolsFolder.addFolder('Spritesheet');
    spritesFolder = sprites;
    sprites.icon(materialIcon('grid-on'));
    sprites.$title.title = 'Drop an image on the canvas to inspect it as a spritesheet.';
    actionRow(sprites, [
      { key: 'spriteCopyDefinition', label: 'Definition', icon: 'content-copy', tooltip: 'Copy the complete # @alias = @{image}.Layout{...} definition for the dataset', run: () => spriteEditor.copyDefinition() },
      { key: 'spriteCopyFrame', label: 'Frame', icon: 'select-all', tooltip: 'Copy the @alias[A1] reference for the frame selected on the image', run: () => spriteEditor.copyFrame() },
    ], 'deckmaker-primary-actions');
  settingRow(sprites, [
    { key: 'spriteMode', args: [['Grid', 'Preset', 'Custom']], label: 'Mode', icon: 'grid-on', tooltip: 'Define frames by grid, card preset or custom size', local: true, live: true },
    { key: 'spriteOrder', args: [['Rows first', 'Columns first']], label: 'Order', icon: 'sort', tooltip: 'Number frames by rows or by columns', local: true, live: true },
  ]);
  settingRow(sprites, [
    { key: 'spriteAlias', label: 'Alias', icon: 'alternate-email', tooltip: 'Alias used in generated frame references', local: true, live: true, inputType: 'text', attributes: { placeholder: 'Alias', autocomplete: 'off', spellcheck: 'false' } },
    { key: 'spriteDpi', args: [1, 2400, 1], label: 'DPI', icon: 'high-quality', tooltip: 'Resolution used to convert dropped image pixels to millimetres', local: true, live: true },
  ]);
  settingRow(sprites, [
    { key: 'spritePreset', args: [Object.keys(options.cardPresets)], label: 'Preset', icon: 'playing-cards', tooltip: 'Known card or counter size', local: true, live: true },
  ]);
  settingRow(sprites, [
    { key: 'spriteCols', args: [1, 30, 1], label: '', icon: 'view-column', tooltip: 'Number of columns (1–30)', iconOnly: true, local: true, live: true },
    { key: 'spriteRows', args: [1, 30, 1], label: '', icon: 'view-stream', tooltip: 'Number of rows (1–30)', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row');
  settingRow(sprites, [
    { key: 'spriteWidth', args: [1, 1000, .1], label: '', icon: 'width', tooltip: 'Custom frame width in millimetres', iconOnly: true, local: true, live: true },
    { key: 'spriteHeight', args: [1, 1000, .1], label: '', icon: 'height', tooltip: 'Custom frame height in millimetres', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row');
  labeledSettingRow(sprites, 'Gaps', [
    { key: 'spriteGapH', args: [0, 30, .1], label: '', icon: 'horizontal-distribute', tooltip: 'Horizontal gap in millimetres (0–30)', iconOnly: true, local: true, live: true },
    { key: 'spriteGapV', args: [0, 30, .1], label: '', icon: 'vertical-distribute', tooltip: 'Vertical gap in millimetres (0–30)', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row');
  settingRow(sprites, [
    { key: 'spriteLeft', args: [0, 30, .1], label: '', icon: 'align-horizontal-left', tooltip: 'Left border in millimetres (0–30)', iconOnly: true, local: true, live: true },
    { key: 'spriteRight', args: [0, 30, .1], label: '', icon: 'align-horizontal-right', tooltip: 'Right border in millimetres (0–30)', iconOnly: true, local: true, live: true },
  ], 'deckmaker-number-row deckmaker-sprite-compact-row');
    settingRow(sprites, [
      { key: 'spriteTop', args: [0, 30, .1], label: '', icon: 'vertical-align-top', tooltip: 'Top border in millimetres (0–30)', iconOnly: true, local: true, live: true },
      { key: 'spriteBottom', args: [0, 30, .1], label: '', icon: 'vertical-align-bottom', tooltip: 'Bottom border in millimetres (0–30)', iconOnly: true, local: true, live: true },
    ], 'deckmaker-number-row deckmaker-sprite-compact-row');
    const definitionController = decorate(sprites.add(model, 'spriteDefinition'), {
      label: 'Result', icon: 'data-object', tooltip: 'Definition ready to paste into a dataset comment row',
    });
    definitionController.disable();
    controllers.push(definitionController);
  }

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
  [datasetFolder, exportFolder, sprites, toolsFolder, play, advanced, configuration, help].filter(Boolean).forEach(folder => folder.close());
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
  const sheetId = String(state.sheetId || '').trim();
  const sheetGid = String(state.sheetGid ?? '').trim();
  const sheetUrl = sheetEditorUrl(state);
  const sheetKey = `${sheetId}:${sheetGid}:${sheetViewMode}`;
  if (elements.sheetEditorFrame.dataset.sheetKey !== sheetKey) {
    elements.sheetEditorFrame.dataset.sheetKey = sheetKey;
    elements.sheetEditorFrame.src = sheetUrl || 'about:blank';
  }
  initializeSplitter(Boolean(sheetId));
  if (!spriteEditor?.active) previewViewer.setPreview(state.preview);
  elements.sheetEditor.hidden = false;
  elements.sheetEditor.classList.toggle('empty', !sheetId);
  elements.sheetEditorFrame.hidden = !sheetId;
  elements.sheetEditorMessage.hidden = Boolean(sheetId);
  const sheetStatus = String(state.sheetStatus || '');
  elements.sheetEditorMessage.textContent = !sheetId
    ? 'Drag and drop a Google Sheets URL here'
    : sheetStatus === 'error'
      ? 'Could not resolve the Google Sheets tab. Check access or select a numeric gid.'
      : 'Resolving sheet tab…';
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
  if (busy && !wasBusy && state.job !== 'preview') logDisplayMode = 'auto';
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
  const lines = (events || [])
    .filter(event => event?.kind === 'log' && event.text)
    .map(event => String(event.text));
  if (!lines.length) return;
  const followsTail = !elements.log.textContent
    || elements.log.scrollHeight - elements.log.scrollTop - elements.log.clientHeight < 24;
  const current = elements.log.textContent ? elements.log.textContent.split('\n') : [];
  elements.log.textContent = current.concat(lines).slice(-500).join('\n');
  if (followsTail) elements.log.scrollTop = elements.log.scrollHeight;
}

async function poll() {
  while (true) {
    try {
      const data = await api(`/api/events?after=${sequence}&timeout=5`);
      sequence = Math.max(sequence, Number(data.seq || 0));
      appendEventLogs(data.events);
      if (data.state) applyState(data.state);
    } catch (error) {
      toast(error.message);
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
}

let dropDepth = 0;

function imageFileFromTransfer(dataTransfer) {
  return Array.from(dataTransfer?.files || []).find(file => (
    String(file.type || '').startsWith('image/')
    || /\.(?:png|jpe?g|webp|gif|svg|bmp|avif)$/i.test(String(file.name || ''))
  )) || null;
}

function isCanvasImageDrag(event) {
  return elements.previewViewport.contains(event.target)
    && (imageFileFromTransfer(event.dataTransfer) || Array.from(event.dataTransfer?.types || []).includes('Files'));
}

function clearDropFeedback() {
  document.body.classList.remove('url-drop-active', 'image-drop-active');
  datasetFolder?.domElement.classList.remove('deckmaker-drop-target');
}

window.addEventListener('dragenter', event => {
  event.preventDefault();
  dropDepth += 1;
  if (isCanvasImageDrag(event)) document.body.classList.add('image-drop-active');
  else {
    document.body.classList.add('url-drop-active');
    datasetFolder?.domElement.classList.add('deckmaker-drop-target');
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
    ? imageFileFromTransfer(event.dataTransfer)
    : null;
  if (image) {
    if (spriteEditor?.loadFile(image)) {
      gui?.open();
      toolsFolder?.open();
      spritesFolder?.open();
      toast(`Spritesheet image loaded: ${image.name}`);
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
  setGuiTitle(
    datasetFolder,
    `Dataset [gsheet://${sheet.sheetId.slice(0, 10)}${sheet.sheetId.length > 10 ? '...' : ''}]`,
    sheet.url,
  );
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
  await openBrowserSession();
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
