function clipboardCellText(cell) {
  const copy = cell.cloneNode(true);
  for (const lineBreak of copy.querySelectorAll('br')) lineBreak.replaceWith('\n');
  return copy.textContent || '';
}

export function parseClipboardHtml(html) {
  if (!html || typeof DOMParser === 'undefined') return null;
  const document = new DOMParser().parseFromString(html, 'text/html');
  const table = document.querySelector('table');
  if (!table) return null;
  const rows = [...table.querySelectorAll('tr')]
    .map(row => [...row.querySelectorAll(':scope > th, :scope > td')].map(clipboardCellText))
    .filter(row => row.length);
  return rows.length ? rows : null;
}

export function parseClipboardRange(clipboard) {
  const text = String(clipboard ?? '');
  const rows = [[]];
  let value = '';
  let quoted = false;
  let fieldStart = true;

  const pushValue = () => {
    rows[rows.length - 1].push(value);
    value = '';
    fieldStart = true;
  };

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        value += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else if (character === '\r' && text[index + 1] === '\n') {
        value += '\n';
        index += 1;
      } else {
        value += character;
      }
      continue;
    }

    if (fieldStart && character === '"') {
      quoted = true;
      fieldStart = false;
    } else if (character === '\t') {
      pushValue();
    } else if (character === '\n' || character === '\r') {
      if (character === '\r' && text[index + 1] === '\n') index += 1;
      pushValue();
      rows.push([]);
    } else {
      value += character;
      fieldStart = false;
    }
  }

  pushValue();
  if (rows.length > 1 && rows.at(-1).length === 1 && rows.at(-1)[0] === ''
      && /(?:\r\n|\r|\n)$/.test(text)) rows.pop();
  return rows;
}

function quoteClipboardValue(value) {
  const text = String(value ?? '');
  return /["\t\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function formatClipboardRange(rows) {
  return rows.map(row => row.map(quoteClipboardValue).join('\t')).join('\r\n');
}

export function mapClipboardRange(table, data) {
  const range = table?.modules?.selectRange?.activeRange;
  if (!range || !data.length) return false;
  const bounds = range.getBounds();
  const startCell = bounds.start;
  if (!startCell) return false;

  const visibleColumns = table.columnManager.getVisibleColumnsByIndex();
  const startColumn = visibleColumns.indexOf(startCell.column);
  if (startColumn < 0) return false;
  const columnCount = bounds.start === bounds.end
    ? Math.max(1, ...data.map(row => row.length))
    : visibleColumns.indexOf(bounds.end.column) - startColumn + 1;
  const columns = visibleColumns.slice(startColumn, startColumn + columnCount);

  return data.map(values => {
    const row = {};
    const source = values.length ? values : [''];
    columns.forEach((column, index) => {
      row[column.field] = source[index % source.length];
    });
    return row;
  });
}
