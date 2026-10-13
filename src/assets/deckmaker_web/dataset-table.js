import { DatasetContextMenu } from './dataset-context-menu.js';

const TABULATOR_MODULE_URL = 'https://unpkg.com/tabulator-tables@6.5.3/dist/js/tabulator_esm.min.js';
const ROW_KINDS = [
  'dataset-header', 'directive', 'comment', 'comment-marker',
  'comment-block', 'data', 'orphan', 'blank',
];

let tabulatorConstructor;

export function stickyHeaderCount(headers, scrollTop, rowHeight) {
  let count = 0;
  for (const header of headers) {
    const threshold = Math.max(0, (header.position - count) * rowHeight);
    if (scrollTop <= threshold) break;
    count += 1;
  }
  return count;
}

function syntaxTokens(value, kind) {
  if (!value || ['comment', 'comment-marker', 'comment-block'].includes(kind)) return [];
  const tokens = [];
  const addMatches = (pattern, group = 1, includePrefix = false) => {
    for (const match of value.matchAll(pattern)) {
      const identifier = match[group];
      if (!identifier) continue;
      const identifierOffset = match[0].indexOf(identifier);
      const identifierStart = match.index + identifierOffset;
      const start = includePrefix ? match.index + Math.max(0, identifierOffset - 1) : identifierStart;
      const end = identifierStart + identifier.length;
      if (tokens.some(token => start < token.end && end > token.start)) continue;
      tokens.push({start, end, identifierStart, identifierEnd: end});
    }
  };
  addMatches(/:([A-Za-z_][A-Za-z0-9_.-]*)(?=\s*(?:\(|:))/g, 1, true);
  addMatches(/@([A-Za-z_][A-Za-z0-9_.-]*)(?=\s*(?:\[|\{|=))/g, 1, true);
  addMatches(/\$\{([A-Za-z_][A-Za-z0-9_.-]*)/g);
  addMatches(/(?:^|[\s,[({])([A-Za-z_][A-Za-z0-9_.-]*)(?=\s*(?:~|\.T\{|\.Layout\{|\[))/g);
  if (kind === 'directive') {
    addMatches(/^\s*#\s*([A-Za-z_][A-Za-z0-9_.-]*)(?=\s*(?:\(|=))/g);
  }
  return tokens.sort((left, right) => left.start - right.start || left.end - right.end);
}

function renderSemanticText(target, value, kind) {
  const tokens = syntaxTokens(value, kind);
  let cursor = 0;
  for (const token of tokens) {
    if (token.start > cursor) target.append(document.createTextNode(value.slice(cursor, token.start)));
    const syntax = document.createElement('span');
    syntax.className = 'dataset-syntax-token';
    if (token.identifierStart > token.start) {
      syntax.append(document.createTextNode(value.slice(token.start, token.identifierStart)));
    }
    const identifier = document.createElement('strong');
    identifier.className = 'dataset-syntax-identifier';
    identifier.textContent = value.slice(token.identifierStart, token.identifierEnd);
    syntax.append(identifier);
    target.append(syntax);
    cursor = token.end;
  }
  if (cursor < value.length) target.append(document.createTextNode(value.slice(cursor)));
}

async function loadTabulator() {
  if (!tabulatorConstructor) {
    tabulatorConstructor = import(TABULATOR_MODULE_URL)
      .then(module => module.TabulatorFull || module.Tabulator)
      .catch(error => {
        tabulatorConstructor = null;
        throw error;
      });
  }
  return tabulatorConstructor;
}

export class DatasetTable {
  constructor(
    { container, message, reportError, reportStatus },
    requestData,
    saveCell,
    changeStructure,
    onCellSelected = null,
  ) {
    this.container = container;
    this.message = message;
    this.reportError = reportError || (() => {});
    this.reportStatus = reportStatus || (() => {});
    this.requestData = requestData;
    this.saveCell = saveCell;
    this.changeStructure = changeStructure;
    this.onCellSelected = onCellSelected || (() => {});
    this.table = null;
    this.key = '';
    this.loadingKey = '';
    this.revision = '';
    this.editor = null;
    this.saveChain = Promise.resolve();
    this.pendingEdits = new Map();
    this.savedValues = new Map();
    this.saveTimer = 0;
    this.columnSignature = '';
    this.resizingSelection = false;
    this.suppressChanges = false;
    this.saving = false;
    this.pollingRevision = false;
    this.suppressSelection = false;
    this.spillLayoutFrame = 0;
    this.stickyHeaderFrame = 0;
    this.stickyHeaders = [];
    this.stickyHeadersVisible = 0;
    this.stickyScrollTop = 0;
    this.columnResizeObserver = new ResizeObserver(() => this.scheduleSpillLayout());
    this.rowHeight = 22;
    this.contextMenu = new DatasetContextMenu(this.contextMenuActions());
    this.container.style.setProperty('--dataset-row-height', `${this.rowHeight}px`);
    this.revisionTimer = setInterval(() => this.refreshExternalChanges(), 1000);
  }

  hide() {
    this.flushEdits();
    this.contextMenu.close();
    this.container.hidden = true;
    this.loadingKey = '';
  }

  redraw(force = false) {
    if (!this.container.hidden) {
      this.table?.redraw(force);
      this.scheduleSpillLayout();
    }
  }

  indexStickyHeaders(rows) {
    if (this.stickyHeaderFrame) cancelAnimationFrame(this.stickyHeaderFrame);
    this.stickyHeaderFrame = 0;
    this.stickyHeadersVisible = 0;
    this.stickyHeaders = (rows || []).flatMap((row, position) => (
      row.__kind__ === 'dataset-header' ? [{id: row.__csv_row__, position}] : []
    ));
  }

  scheduleStickyHeaders(scrollTop) {
    this.stickyScrollTop = scrollTop;
    if (this.stickyHeaderFrame) return;
    this.stickyHeaderFrame = requestAnimationFrame(() => {
      this.stickyHeaderFrame = 0;
      this.updateStickyHeaders(this.stickyScrollTop);
    });
  }

  updateStickyHeaders(scrollTop) {
    if (!this.table || !this.stickyHeaders.length) return;
    const desired = stickyHeaderCount(this.stickyHeaders, scrollTop, this.rowHeight);
    if (desired === this.stickyHeadersVisible) return;
    const holder = this.container.querySelector('.tabulator-tableholder');
    const retainedScrollTop = holder?.scrollTop ?? scrollTop;
    this.table.blockRedraw();
    try {
      if (desired > this.stickyHeadersVisible) {
        for (let index = this.stickyHeadersVisible; index < desired; index += 1) {
          const row = this.table.getRow(this.stickyHeaders[index].id);
          if (row) row.freeze();
        }
      } else {
        for (let index = this.stickyHeadersVisible - 1; index >= desired; index -= 1) {
          const row = this.table.getRow(this.stickyHeaders[index].id);
          if (row) row.unfreeze();
        }
      }
      this.stickyHeadersVisible = desired;
    } finally {
      this.table.restoreRedraw();
    }
    if (holder) {
      const restoreScroll = () => {
        if (Math.abs(holder.scrollTop - retainedScrollTop) > 0.5) {
          holder.scrollTop = retainedScrollTop;
        }
      };
      restoreScroll();
      requestAnimationFrame(restoreScroll);
    }
  }

  columns(dataset) {
    return (dataset.columns || []).map(column => ({
      title: column.title,
      field: column.field,
      formatter: (cell, _parameters, onRendered) => this.formatCell(cell, onRendered),
      minWidth: 80,
      width: 140,
      tooltip: true,
      cssClass: column.column === 0 ? 'dataset-primary-cell' : '',
    }));
  }

  contextMenuActions() {
    return [
      {
        id: 'insert-row-before',
        label: 'Insert row above',
        icon: 'add-row-above',
        contexts: ['row'],
        run: context => this.structure('insert-row-before', context.row.getData().__csv_row__),
      },
      {
        id: 'insert-row-after',
        label: 'Insert row below',
        icon: 'add-row-below',
        contexts: ['row'],
        run: context => this.structure('insert-row-after', context.row.getData().__csv_row__),
      },
      {
        id: 'row-height',
        label: 'Row height',
        icon: 'height',
        contexts: ['row'],
        children: [
          { id: 'row-height-compact', label: 'Compact (20 px)', run: () => this.setRowHeight(20) },
          { id: 'row-height-normal', label: 'Normal (22 px)', run: () => this.setRowHeight(22) },
          { id: 'row-height-large', label: 'Large (30 px)', run: () => this.setRowHeight(30) },
        ],
      },
      {
        id: 'delete-row',
        label: 'Delete row',
        icon: 'delete',
        contexts: ['row'],
        danger: true,
        run: context => this.structure('delete-row', context.row.getData().__csv_row__),
      },
      {
        id: 'insert-column-before',
        label: 'Insert column left',
        icon: 'add-column-left',
        contexts: ['column'],
        run: context => this.structure('insert-column-before', this.contextColumnIndex(context.column)),
      },
      {
        id: 'insert-column-after',
        label: 'Insert column right',
        icon: 'add-column-right',
        contexts: ['column'],
        run: context => this.structure('insert-column-after', this.contextColumnIndex(context.column)),
      },
      {
        id: 'delete-column',
        label: 'Delete column',
        icon: 'delete',
        contexts: ['column'],
        danger: true,
        run: context => this.structure('delete-column', this.contextColumnIndex(context.column)),
      },
    ];
  }

  contextColumnIndex(column) {
    return Number(String(column.getField() || '').replace(/^column_/, ''));
  }

  installContextMenuEvents() {
    this.table.on('rowContext', (event, row) => {
      this.contextMenu.open(event, { type: 'row', row, table: this.table });
    });
    this.table.on('headerContext', (event, column) => {
      if (!String(column.getField() || '').startsWith('column_')) return;
      this.contextMenu.open(event, { type: 'column', column, table: this.table });
    });
  }

  notifyCellSelected(cell) {
    if (this.suppressSelection || !cell) return;
    const field = String(cell.getField() || '');
    if (!field.startsWith('column_')) return;
    this.onCellSelected({
      row: Number(cell.getRow().getData().__csv_row__ || 0),
      column: Number(field.replace(/^column_/, '')),
    });
  }

  async selectCell(rowNumber, columnNumber) {
    if (!this.table) return false;
    const row = this.table.getRow(Number(rowNumber));
    if (!row) return false;
    const cell = row.getCell(`column_${Number(columnNumber)}`);
    if (!cell) return false;
    this.suppressSelection = true;
    try {
      await this.table.scrollToRow(row, 'center', false);
      await this.table.scrollToColumn(cell.getColumn(), 'center', false);
      this.table.getRanges().forEach(range => range.remove());
      this.table.addRange(cell, cell);
    } finally {
      this.suppressSelection = false;
    }
    return true;
  }

  formatCell(cell, onRendered) {
    const value = String(cell.getValue() ?? '');
    const hasValue = Boolean(value.trim());
    const element = cell.getElement();
    const column = Number(cell.getField().replace(/^column_/, ''));
    const commentedColumns = cell.getRow().getData().__commented_columns__ || [];
    const kind = String(cell.getRow().getData().__kind__ || 'blank');
    element.classList.toggle('dataset-cell-empty', !hasValue);
    element.classList.toggle('dataset-cell-filled', hasValue);
    element.classList.toggle('dataset-cell-commented', commentedColumns.includes(column));

    const content = document.createElement('span');
    content.className = 'dataset-cell-value';
    renderSemanticText(content, value, kind);
    onRendered(() => this.layoutCellSpill(cell, content));
    return content;
  }

  layoutCellSpill(cell, content = null) {
    const cells = cell.getRow().getCells();
    const position = cells.findIndex(candidate => candidate.getField() === cell.getField());
    const element = cell.getElement();
    const value = content || element.querySelector('.dataset-cell-value');
    if (position < 0 || !value) return;
    let availableWidth = element.getBoundingClientRect().width;
    for (let index = position + 1; index < cells.length; index += 1) {
      if (String(cells[index].getValue() ?? '')) break;
      availableWidth += cells[index].getElement().getBoundingClientRect().width;
    }
    value.style.width = `${Math.max(0, availableWidth - 14)}px`;
  }

  scheduleSpillLayout() {
    cancelAnimationFrame(this.spillLayoutFrame);
    this.spillLayoutFrame = requestAnimationFrame(() => {
      this.spillLayoutFrame = 0;
      this.table?.getRows().forEach(row => {
        row.getCells().forEach(cell => this.layoutCellSpill(cell));
      });
    });
  }

  observeColumnWidths() {
    this.columnResizeObserver.disconnect();
    this.table?.getColumns().forEach(column => {
      this.columnResizeObserver.observe(column.getElement());
    });
    this.scheduleSpillLayout();
  }

  formatRow(row) {
    const element = row.getElement();
    ROW_KINDS.forEach(kind => element.classList.remove(`dataset-row-${kind}`));
    const kind = String(row.getData().__kind__ || 'blank');
    element.classList.add(`dataset-row-${kind}`);
  }

  captureSavedValues(rows) {
    this.savedValues.clear();
    for (const row of rows || []) {
      const rowNumber = Number(row.__csv_row__ || 0);
      if (!rowNumber) continue;
      for (const [field, rawValue] of Object.entries(row)) {
        const match = field.match(/^column_(\d+)$/);
        if (match) this.savedValues.set(`${rowNumber}:${match[1]}`, String(rawValue ?? ''));
      }
    }
  }

  trackChanges(rows) {
    if (this.suppressChanges) return;
    for (const row of rows || []) {
      const rowNumber = Number(row.__csv_row__ || 0);
      if (!rowNumber) continue;
      for (const [field, rawValue] of Object.entries(row)) {
        const match = field.match(/^column_(\d+)$/);
        if (!match) continue;
        const column = Number(match[1]);
        const value = String(rawValue ?? '');
        const key = `${rowNumber}:${column}`;
        if (this.savedValues.get(key) !== value) {
          this.pendingEdits.set(key, {row: rowNumber, column, value});
        }
      }
    }
    if (!this.pendingEdits.size) return;
    this.scheduleSpillLayout();
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.flushEdits(), 40);
  }

  applyRowSemantics(rowKinds, commentedColumns) {
    const affectedRows = new Set([
      ...Object.keys(rowKinds || {}),
      ...Object.keys(commentedColumns || {}),
    ]);
    affectedRows.forEach(rowNumber => {
      const row = this.table?.getRow(Number(rowNumber));
      if (row) {
        row.update({
          __kind__: rowKinds?.[rowNumber] ?? row.getData().__kind__,
          __commented_columns__: commentedColumns?.[rowNumber] ?? row.getData().__commented_columns__,
        }).then(() => row.getCells().forEach(cell => cell.reformat()));
      }
    });
  }

  isolateEditorEvents(cell) {
    requestAnimationFrame(() => {
      const editor = cell.getElement().querySelector('input, textarea, select, [contenteditable="true"]');
      if (!editor || editor.dataset.pnpinkKeyboardGuard) return;
      editor.dataset.pnpinkKeyboardGuard = 'true';
      ['pointerdown', 'mousedown', 'mouseup', 'click', 'dblclick'].forEach(type => {
        editor.addEventListener(type, event => event.stopPropagation());
      });
      editor.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.isComposing) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.altKey && typeof editor.setRangeText === 'function') {
          const start = editor.selectionStart ?? editor.value.length;
          const end = editor.selectionEnd ?? start;
          editor.setRangeText('\n', start, end, 'end');
          editor.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            inputType: 'insertLineBreak',
            data: '\n',
          }));
          return;
        }
        editor.blur();
      }, true);
      editor.addEventListener('keydown', event => {
        if (!['Enter', 'Escape', 'Tab'].includes(event.key)) event.stopPropagation();
      });
    });
  }

  flushEdits() {
    clearTimeout(this.saveTimer);
    this.saveTimer = 0;
    if (!this.pendingEdits.size) return this.saveChain;
    const updates = [...this.pendingEdits.values()];
    this.pendingEdits.clear();
    updates.forEach(update => {
      this.savedValues.set(`${update.row}:${update.column}`, update.value);
    });
    this.saveChain = this.saveChain.then(async () => {
      this.saving = true;
      try {
        const result = await this.saveCell({
          updates,
          revision: this.revision,
        });
        this.revision = String(result.revision || this.revision);
        this.applyRowSemantics(result.rowKinds, result.commentedColumns);
        this.table?.clearCellEdited();
      } finally {
        this.saving = false;
      }
    }).catch(error => {
      this.reportError(`Could not save the CSV dataset: ${error.message}`);
      this.key = '';
      if (this.editor) this.show(this.editor);
    });
    return this.saveChain;
  }

  async refreshExternalChanges() {
    if (this.pollingRevision || this.container.hidden || !this.editor?.url
        || this.loadingKey || this.saving || this.saveTimer || this.pendingEdits.size) return;
    this.pollingRevision = true;
    try {
      const result = await this.requestData('/api/dataset/revision');
      const revision = String(result.revision || '');
      if (revision && revision !== this.revision
          && !this.saving && !this.saveTimer && !this.pendingEdits.size) {
        this.key = '';
        await this.show({...this.editor, revision});
      }
    } catch (_error) {
      // A transient polling failure must not interrupt editing.
    } finally {
      this.pollingRevision = false;
    }
  }

  resizeSelectedColumns(column) {
    if (this.resizingSelection || !this.table) return;
    const tableRows = this.table.getRows();
    const columns = new Set();
    for (const range of column.getRanges()) {
      if (range.getRows().length !== tableRows.length) continue;
      range.getColumns().forEach(selected => columns.add(selected));
    }
    if (columns.size >= 2) {
      this.resizingSelection = true;
      const width = column.getWidth();
      columns.forEach(selected => {
        if (selected !== column) selected.setWidth(width);
      });
      this.resizingSelection = false;
    }
    this.scheduleSpillLayout();
  }

  setRowHeight(height) {
    this.rowHeight = height;
    this.container.style.setProperty('--dataset-row-height', `${height}px`);
    this.redraw(true);
    this.scheduleStickyHeaders(this.container.querySelector('.tabulator-tableholder')?.scrollTop || 0);
  }

  selectAll() {
    if (!this.table) return;
    const rows = this.table.getRows();
    if (!rows.length) return;
    const firstCells = rows[0].getCells();
    const lastCells = rows[rows.length - 1].getCells();
    if (!firstCells.length || !lastCells.length) return;
    this.table.getRanges().forEach(range => range.remove());
    this.table.addRange(firstCells[0], lastCells[lastCells.length - 1]);
  }

  async structure(action, index) {
    try {
      await this.flushEdits();
      const result = await this.changeStructure({
        action,
        index,
        revision: this.revision,
      });
      this.revision = String(result.revision || this.revision);
      this.key = '';
      if (this.editor) await this.show({...this.editor, revision: this.revision});
    } catch (error) {
      this.reportError(`Could not modify the CSV dataset: ${error.message}`);
    }
  }

  async show(editor) {
    if (this.editor?.url && this.editor.url !== editor.url) await this.flushEdits();
    this.editor = editor;
    const key = `${editor.url || ''}:${editor.revision || ''}`;
    this.container.hidden = false;
    if (this.table && this.key === key) {
      this.message.hidden = true;
      return;
    }
    if (!editor.url || this.loadingKey === key) return;

    this.loadingKey = key;
    this.message.hidden = true;
    this.reportStatus('Loading local CSV dataset\u2026');
    try {
      const [Tabulator, dataset] = await Promise.all([
        loadTabulator(),
        this.requestData(editor.url),
      ]);
      if (this.loadingKey !== key) return;
      if (!Tabulator) throw new Error('Tabulator did not expose a browser constructor');

      if (!this.table) {
        this.indexStickyHeaders(dataset.rows || []);
        this.columnSignature = (dataset.columns || []).map(column => column.field).join('|');
        this.captureSavedValues(dataset.rows || []);
        this.table = new Tabulator(this.container, {
          index: '__csv_row__',
          data: dataset.rows || [],
          columns: this.columns(dataset),
          height: '100%',
          layout: 'fitData',
          placeholder: 'The CSV dataset is empty',
          movableColumns: false,
          selectableRange: true,
          selectableRangeColumns: true,
          selectableRangeRows: true,
          selectableRangeClearCells: true,
          selectableRangeClearCellsValue: '',
          selectableRangeBlurEditOnNavigate: false,
          editTriggerEvent: 'dblclick',
          editorEmptyValue: '',
          history: true,
          keybindings: {
            scrollPageUp: false,
            scrollPageDown: false,
            scrollToStart: false,
            scrollToEnd: false,
          },
          clipboard: true,
          clipboardCopyStyled: false,
          clipboardCopyRowRange: 'range',
          clipboardCopyConfig: {
            rowHeaders: false,
            columnHeaders: false,
          },
          clipboardPasteParser: 'range',
          clipboardPasteAction: 'range',
          resizableColumnGuide: true,
          rowFormatter: row => this.formatRow(row),
          columnDefaults: {
            headerSort: false,
            headerHozAlign: 'center',
            editor: 'textarea',
            editorParams: {
              verticalNavigation: 'editor',
            },
            resizable: 'header',
          },
          rowHeader: {
            title: '',
            field: '__csv_row__',
            formatter: cell => String(cell.getRow().getData().__csv_row__ ?? ''),
            headerSort: false,
            headerClick: () => this.selectAll(),
            headerTooltip: 'Select all cells',
            hozAlign: 'center',
            frozen: true,
            width: 44,
            resizable: false,
            cssClass: 'range-header-col',
            editor: false,
          },
        });
        this.table.on('cellEditing', cell => this.isolateEditorEvents(cell));
        this.table.on('cellClick', (_event, cell) => this.notifyCellSelected(cell));
        this.table.on('cellTap', (_event, cell) => this.notifyCellSelected(cell));
        this.table.on('dataChanged', rows => this.trackChanges(rows));
        this.table.on('columnResized', column => this.resizeSelectedColumns(column));
        this.table.on('scrollVertical', top => this.scheduleStickyHeaders(top));
        this.installContextMenuEvents();
        requestAnimationFrame(() => this.observeColumnWidths());
      } else {
        this.indexStickyHeaders([]);
        const signature = (dataset.columns || []).map(column => column.field).join('|');
        if (signature !== this.columnSignature) {
          this.columnSignature = signature;
          this.table.setColumns(this.columns(dataset));
          requestAnimationFrame(() => this.observeColumnWidths());
        }
        this.suppressChanges = true;
        try {
          await this.table.replaceData(dataset.rows || []);
          this.indexStickyHeaders(dataset.rows || []);
          this.captureSavedValues(dataset.rows || []);
        } finally {
          this.suppressChanges = false;
        }
        this.table.clearHistory();
      }
      this.revision = String(dataset.revision || editor.revision || '');
      this.key = key;
      this.message.hidden = true;
      requestAnimationFrame(() => this.redraw());
    } catch (error) {
      if (this.loadingKey !== key) return;
      this.reportError(`Could not open the local CSV dataset: ${error.message}`);
    } finally {
      if (this.loadingKey === key) this.loadingKey = '';
    }
  }
}
