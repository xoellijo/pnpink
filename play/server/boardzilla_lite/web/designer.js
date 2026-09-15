import { XGUI } from '/static/lil-xgui.min.js';

const gameName = decodeURIComponent(location.pathname.split('/').pop());
const menuNode = document.querySelector('#designer-menu');
const boardNode = document.querySelector('#designer-board');
const leftToolboxNode = document.querySelector('#designer-toolbox-left');
const rightToolboxNode = document.querySelector('#designer-toolbox-right');
const titleNode = document.querySelector('#designer-title');
const statusNode = document.querySelector('#designer-status');
const saveButton = document.querySelector('#designer-save');
const autosaveNode = document.querySelector('#designer-autosave');

let manifest;
let schema;
let revision;
let dirty = false;
let saving = false;
let saveTimer;
let designerGUI;
let areasFolder;
let selection = { type: 'root' };
const entityFolders = new Map();

function setStatus(message, state = '') {
  statusNode.textContent = message;
  statusNode.dataset.state = state;
}

function resolveSchema(definition, currentValue) {
  if (!definition) return {};
  if (definition.$ref) {
    return definition.$ref.split('/').slice(1).reduce((current, part) => current?.[part], schema) || {};
  }
  if (definition.oneOf) {
    return resolveSchema(definition.oneOf.find(candidate => {
      const candidateSchema = resolveSchema(candidate, currentValue);
      return Array.isArray(currentValue) ? candidateSchema.type === 'array' : candidateSchema.type === 'object';
    }) || definition.oneOf[0], currentValue);
  }
  return definition;
}

function propertyTitle(key, definition) {
  return definition.title || key.replace(/([A-Z])/g, ' $1').replace(/^./, value => value.toUpperCase());
}

function defaultValue(definition) {
  const resolved = resolveSchema(definition);
  if (resolved.default !== undefined) return structuredClone(resolved.default);
  if (resolved.type === 'object') return {};
  if (resolved.type === 'array') return [];
  if (resolved.type === 'boolean') return false;
  if (resolved.type === 'integer' || resolved.type === 'number') return resolved.minimum ?? 0;
  return '';
}

function componentList() {
  if (Array.isArray(manifest.pieces)) return manifest.pieces;
  manifest.pieces.extra ||= [];
  return manifest.pieces.extra;
}

function normalizeArea(value) {
  if (!value || !manifest.areas.includes(value)) return;
  value.layout ||= {};
  const fallback = value.layout.shared || value.layout.owner || { left: 35, top: 35, width: 30, height: 30 };
  if (value.toolbox) return;
  if (value.scope === 'shared') value.layout.shared ||= structuredClone(fallback);
  else {
    value.layout.owner ||= structuredClone(fallback);
    value.layout.opponent ||= { left: 3, top: 56, width: 72, height: 41 };
  }
}

function dynamicOptions(target, key, definition, required) {
  let values = definition.enum;
  if (key === 'toolbox') values = (manifest.toolboxes || []).map(toolbox => toolbox.id);
  if (key === 'area' && Object.hasOwn(target, 'area')) values = manifest.areas.map(area => area.id);
  if (!values) return undefined;
  return Object.fromEntries([
    ...(!required ? [['(not set)', '']] : []),
    ...values.map(value => [String(value), value]),
  ]);
}

function addObjectControls(gui, target, objectSchema) {
  for (const [key, rawDefinition] of Object.entries(objectSchema.properties || {})) {
    const definition = resolveSchema(rawDefinition, target[key]);
    const required = (objectSchema.required || []).includes(key);
    const title = propertyTitle(key, definition);
    if (definition.type === 'object') {
      if (!target[key] && !required) continue;
      target[key] ||= {};
      const folder = gui.addFolder(title);
      addObjectControls(folder, target[key], definition);
      folder.close?.();
      continue;
    }
    if (definition.type === 'array') {
      const itemSchema = resolveSchema(definition.items);
      if (!['string', 'number', 'integer'].includes(itemSchema.type)) continue;
      const state = { [key]: (target[key] || []).join(', ') };
      gui.add(state, key).name(title).onChange(value => {
        target[key] = String(value).split(',').map(item => item.trim()).filter(Boolean).map(item => itemSchema.type === 'string' ? item : Number(item));
        changed();
      });
      continue;
    }
    const state = { [key]: target[key] ?? defaultValue(definition) };
    const options = dynamicOptions(target, key, definition, required);
    let controller;
    if (options) controller = gui.add(state, key, options);
    else if (definition.format === 'color' && gui.addColor) controller = gui.addColor(state, key);
    else {
      controller = gui.add(state, key);
      if (definition.minimum !== undefined) controller.min?.(definition.minimum);
      if (definition.maximum !== undefined) controller.max?.(definition.maximum);
      if (definition.multipleOf !== undefined) controller.step?.(definition.multipleOf);
    }
    controller.name(title);
    if (definition.readOnly) controller.disable();
    controller.onChange(value => {
      if (!required && value === '') delete target[key];
      else target[key] = value;
      normalizeArea(target);
      changed();
      renderPreview();
    });
  }
}

function addAction(gui, name, label, run, className) {
  const state = { [name]: run };
  const controller = gui.add(state, name).name(label);
  if (className) controller.domElement.classList.add(className);
  return controller;
}

function entityKey(type, index) {
  return `${type}:${index}`;
}

function selectEntity(type, index, open = false) {
  selection = { type, index };
  designerGUI?.domElement.querySelectorAll('.designer-selected').forEach(node => node.classList.remove('designer-selected'));
  const folder = entityFolders.get(entityKey(type, index));
  folder?.domElement.classList.add('designer-selected');
  if (open) {
    areasFolder?.open?.();
    folder?.open?.();
    folder?.domElement.scrollIntoView({ block: 'nearest' });
  }
  boardNode.querySelectorAll('[data-area]').forEach(node => node.classList.toggle('selected', type === 'area' && Number(node.dataset.area) === index));
}

function bindEntityFolder(folder, type, index) {
  entityFolders.set(entityKey(type, index), folder);
  folder.domElement.querySelector(':scope > .title')?.addEventListener('click', () => selectEntity(type, index));
  if (selection.type === type && selection.index === index) folder.domElement.classList.add('designer-selected');
}

function uniqueId(prefix, collection) {
  const ids = new Set(collection.map(item => item.id));
  let index = 1;
  while (ids.has(`${prefix}-${index}`)) index += 1;
  return `${prefix}-${index}`;
}

function addEntity(type) {
  if (type === 'toolbox') {
    manifest.toolboxes ||= [];
    const id = uniqueId('toolbox', manifest.toolboxes);
    manifest.toolboxes.push({ id, label: `Toolbox ${manifest.toolboxes.length + 1}`, scope: 'shared', side: 'left' });
    selection = { type: 'toolbox', index: manifest.toolboxes.length - 1 };
  } else if (type === 'area') {
    const id = uniqueId('area', manifest.areas);
    manifest.areas.push({
      id, label: `Area ${manifest.areas.length + 1}`, kind: 'free', scope: 'shared', visibility: 'public',
      actions: ['move-from', 'move-to'], layout: { shared: { left: 35, top: 35, width: 30, height: 30 }, rows: 1, columns: 1 },
    });
    selection = { type: 'area', index: manifest.areas.length - 1 };
  } else {
    const pieces = componentList();
    const id = uniqueId('component', pieces);
    pieces.push({ id, label: `Component ${pieces.length + 1}`, type: 'token', copies: 1, color: '#d8b64c', shape: 'circle', initial: { area: manifest.areas[0].id } });
    selection = { type: 'piece', index: pieces.length - 1 };
  }
  changed();
  renderAll();
}

function deleteEntity(type, index) {
  if (type === 'toolbox') {
    const toolbox = manifest.toolboxes[index];
    if (manifest.areas.some(area => area.toolbox === toolbox.id)) return setStatus('Move its areas before deleting this toolbox', 'error');
    manifest.toolboxes.splice(index, 1);
  } else if (type === 'area') {
    if (manifest.areas.length === 1) return setStatus('A game needs at least one area', 'error');
    const area = manifest.areas[index];
    if (componentList().some(piece => piece.initial?.area === area.id)) return setStatus('Move its components before deleting this area', 'error');
    manifest.areas.splice(index, 1);
  } else componentList().splice(index, 1);
  selection = { type: 'root' };
  changed();
  renderAll();
}

function addEntityFolders(parent, type, items, itemSchema) {
  items.forEach((item, index) => {
    const folder = parent.addFolder(item.label || item.id || `${type} ${index + 1}`);
    bindEntityFolder(folder, type, index);
    addObjectControls(folder, item, resolveSchema(itemSchema, item));
    addAction(folder, `delete_${type}_${index}`, `Delete ${type}`, () => deleteEntity(type, index), 'designer-delete');
    folder.close?.();
  });
}

function renderDesignerMenu() {
  designerGUI?.destroy();
  menuNode.innerHTML = '';
  entityFolders.clear();
  designerGUI = new XGUI({ autoPlace: false, title: manifest.name, width: 332, touchStyles: false });
  designerGUI.domElement.classList.add('designer-gui');
  menuNode.appendChild(designerGUI.domElement);

  const gameFolder = designerGUI.addFolder('Game');
  addObjectControls(gameFolder, manifest, {
    type: 'object', required: ['name'], properties: { name: schema.properties.name, description: schema.properties.description },
  });

  manifest.players ||= { min: 2, max: 4 };
  const playersFolder = designerGUI.addFolder('Players');
  addObjectControls(playersFolder, manifest.players, resolveSchema(schema.properties.players, manifest.players));
  playersFolder.close?.();

  manifest.turns ||= { mode: 'free', order: 'right' };
  const turnsFolder = designerGUI.addFolder('Turns');
  addObjectControls(turnsFolder, manifest.turns, resolveSchema(schema.properties.turns, manifest.turns));
  turnsFolder.close?.();

  const boardFolder = designerGUI.addFolder('Board');
  addObjectControls(boardFolder, manifest.board, resolveSchema(schema.properties.board, manifest.board));

  const toolboxesFolder = designerGUI.addFolder(`Toolboxes (${(manifest.toolboxes || []).length})`);
  addAction(toolboxesFolder, 'addToolbox', 'Add toolbox', () => addEntity('toolbox'), 'designer-add');
  addEntityFolders(toolboxesFolder, 'toolbox', manifest.toolboxes || [], schema.$defs.toolbox);
  toolboxesFolder.close?.();

  areasFolder = designerGUI.addFolder(`Areas (${manifest.areas.length})`);
  addAction(areasFolder, 'addArea', 'Add area', () => addEntity('area'), 'designer-add');
  addEntityFolders(areasFolder, 'area', manifest.areas, schema.$defs.area);

  const pieces = componentList();
  const componentsFolder = designerGUI.addFolder(`Components (${pieces.length}${Array.isArray(manifest.pieces) ? '' : ' + JSON'})`);
  addAction(componentsFolder, 'addComponent', 'Add component', () => addEntity('piece'), 'designer-add');
  if (!Array.isArray(manifest.pieces)) {
    const sourceFolder = componentsFolder.addFolder('Items JSON source');
    addObjectControls(sourceFolder, manifest.pieces, resolveSchema(schema.$defs.pieceSource, manifest.pieces));
    sourceFolder.close?.();
  }
  addEntityFolders(componentsFolder, 'piece', pieces, schema.$defs.piece);
  componentsFolder.close?.();

  designerGUI.open?.();
  selectEntity(selection.type, selection.index, selection.type === 'area');
}

function areaBox(area) {
  if (area.toolbox) return undefined;
  return area.scope === 'shared' ? area.layout?.shared : area.layout?.owner;
}

function beginAreaDrag(event, area, index, node) {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  selectEntity('area', index, true);
  const box = areaBox(area);
  if (!box) return;
  const board = node.parentElement.getBoundingClientRect();
  const start = { x: event.clientX, y: event.clientY, ...box };
  const resize = event.target.classList.contains('designer-resize');
  node.setPointerCapture(event.pointerId);
  const move = current => {
    const dx = (current.clientX - start.x) / board.width * 100;
    const dy = (current.clientY - start.y) / board.height * 100;
    if (resize) {
      box.width = Math.max(2, Math.min(100 - box.left, start.width + dx));
      box.height = Math.max(2, Math.min(100 - box.top, start.height + dy));
    } else {
      box.left = Math.max(0, Math.min(100 - box.width, start.left + dx));
      box.top = Math.max(0, Math.min(100 - box.height, start.top + dy));
    }
    Object.assign(node.style, { left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` });
    changed();
  };
  const up = () => {
    node.removeEventListener('pointermove', move);
    node.removeEventListener('pointerup', up);
    renderDesignerMenu();
  };
  node.addEventListener('pointermove', move);
  node.addEventListener('pointerup', up);
}

function renderToolbox(node, side) {
  const toolboxes = (manifest.toolboxes || []).filter(item => (item.side || 'left') === side);
  node.innerHTML = toolboxes.map(toolbox => {
    const areas = manifest.areas.filter(area => area.toolbox === toolbox.id);
    return `<section><strong>${PnPInkPlay.escapeHTML(toolbox.label)}</strong>${areas.map(area => `<button type="button" data-area="${manifest.areas.indexOf(area)}">${PnPInkPlay.escapeHTML(area.label)}</button>`).join('')}</section>`;
  }).join('');
  node.querySelectorAll('[data-area]').forEach(button => button.addEventListener('click', () => selectEntity('area', Number(button.dataset.area), true)));
}

function renderPreview() {
  boardNode.style.aspectRatio = String(manifest.board.aspectRatio || 1.6);
  boardNode.style.background = manifest.board.background || '#173f35';
  boardNode.innerHTML = '';
  const playerPanel = document.createElement('div');
  playerPanel.className = 'designer-player-panel';
  playerPanel.innerHTML = '<strong>Player panel</strong>';
  boardNode.appendChild(playerPanel);
  manifest.areas.forEach((area, index) => {
    const box = areaBox(area);
    if (!box) return;
    const node = document.createElement('button');
    node.type = 'button';
    node.className = `designer-area designer-area-${area.kind}${selection.type === 'area' && selection.index === index ? ' selected' : ''}`;
    node.dataset.area = String(index);
    Object.assign(node.style, { left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` });
    node.innerHTML = `<strong>${PnPInkPlay.escapeHTML(area.label)}</strong><small>${PnPInkPlay.escapeHTML(area.kind)} · ${PnPInkPlay.escapeHTML(area.scope)}</small><i class="designer-resize"></i>`;
    node.addEventListener('pointerdown', event => beginAreaDrag(event, area, index, node));
    (area.scope === 'player' ? playerPanel : boardNode).appendChild(node);
  });
  renderToolbox(leftToolboxNode, 'left');
  renderToolbox(rightToolboxNode, 'right');
}

function changed() {
  dirty = true;
  setStatus('Unsaved changes', 'dirty');
  if (autosaveNode.checked) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveManifest, 650);
  }
}

async function saveManifest() {
  if (!dirty || saving) return;
  saving = true;
  saveButton.disabled = true;
  setStatus('Saving…');
  try {
    const response = await fetch(`/designer-api/games/${encodeURIComponent(gameName)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ manifest, revision }),
    });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    manifest = data.manifest;
    revision = data.revision;
    dirty = false;
    setStatus('Saved · new rooms use this definition', 'saved');
  } catch (error) {
    setStatus(String(error.message || error), 'error');
  } finally {
    saving = false;
    saveButton.disabled = false;
  }
}

async function loadManifest(force = false) {
  const response = await fetch(`/designer-api/games/${encodeURIComponent(gameName)}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(await response.text());
  const data = await response.json();
  if (!force && revision && data.revision === revision) return;
  if (!force && dirty) {
    setStatus('The YAML changed on disk · reload to accept it', 'error');
    return;
  }
  manifest = data.manifest;
  schema = data.schema;
  revision = data.revision;
  dirty = false;
  titleNode.textContent = `${manifest.name} · Game Designer`;
  document.title = `PnPPlay Designer: ${manifest.name}`;
  setStatus(force ? 'Reloaded from disk' : 'Watching play.yaml', 'saved');
  renderAll();
}

function renderAll() {
  renderDesignerMenu();
  renderPreview();
}

saveButton.addEventListener('click', saveManifest);
document.querySelector('#designer-reload').addEventListener('click', () => loadManifest(true).catch(error => setStatus(String(error), 'error')));
autosaveNode.addEventListener('change', () => {
  localStorage.setItem('pnpplayDesignerAutosave', autosaveNode.checked ? '1' : '0');
  if (autosaveNode.checked) saveManifest();
});
autosaveNode.checked = localStorage.getItem('pnpplayDesignerAutosave') !== '0';

loadManifest(true).then(() => {
  setInterval(() => loadManifest(false).catch(error => setStatus(String(error), 'error')), 1200);
}).catch(error => setStatus(String(error), 'error'));
