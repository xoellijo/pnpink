function positive(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : fallback;
}

function format(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  if (Math.abs(number - Math.round(number)) < 1e-9) return String(Math.round(number));
  return number.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

function compact(values) {
  const [top, right, bottom, left] = values;
  if (values.every(value => Math.abs(value - top) < 1e-9)) return format(top);
  if (Math.abs(top - bottom) < 1e-9 && Math.abs(right - left) < 1e-9) return `[${format(top)} ${format(right)}]`;
  return `[${values.map(format).join(' ')}]`;
}

export function columnName(index) {
  let value = Number(index) + 1;
  let result = '';
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + value % 26) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

export function computeSpriteGrid(model, metadata, presets = {}) {
  const width = positive(metadata?.widthMm);
  const height = positive(metadata?.heightMm);
  const margins = [model.spriteTop, model.spriteRight, model.spriteBottom, model.spriteLeft].map(value => positive(value));
  const [top, right, bottom, left] = margins;
  const gapH = positive(model.spriteGapH);
  const gapV = positive(model.spriteGapV);
  const contentWidth = width - left - right;
  const contentHeight = height - top - bottom;
  if (contentWidth <= 0 || contentHeight <= 0) return null;

  let columns;
  let rows;
  let tileWidth;
  let tileHeight;
  const mode = String(model.spriteMode || 'Grid').toLowerCase();
  if (mode === 'preset') {
    const preset = presets[model.spritePreset] || presets.Standard;
    if (!preset) return null;
    tileWidth = positive(preset.width);
    tileHeight = positive(preset.height);
    columns = Math.floor((contentWidth + gapH) / (tileWidth + gapH));
    rows = Math.floor((contentHeight + gapV) / (tileHeight + gapV));
  } else if (mode === 'custom') {
    tileWidth = positive(model.spriteWidth);
    tileHeight = positive(model.spriteHeight);
    columns = Math.floor((contentWidth + gapH) / (tileWidth + gapH));
    rows = Math.floor((contentHeight + gapV) / (tileHeight + gapV));
  } else {
    columns = Math.max(1, Math.round(positive(model.spriteCols, 1)));
    rows = Math.max(1, Math.round(positive(model.spriteRows, 1)));
    tileWidth = (contentWidth - (columns - 1) * gapH) / columns;
    tileHeight = (contentHeight - (rows - 1) * gapV) / rows;
  }
  if (!columns || !rows || tileWidth <= 0 || tileHeight <= 0) return null;

  const cells = [];
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const index = model.spriteOrder === 'Columns first' ? column * rows + row + 1 : row * columns + column + 1;
      cells.push({
        row, column, index,
        address: `${columnName(column)}${row + 1}`,
        x: left + column * (tileWidth + gapH),
        y: top + row * (tileHeight + gapV),
        width: tileWidth,
        height: tileHeight,
      });
    }
  }
  return { width, height, columns, rows, tileWidth, tileHeight, margins, gapH, gapV, cells };
}

export function layoutExpression(grid, model) {
  if (!grid) return '';
  const order = model.spriteOrder === 'Columns first' ? '^' : '';
  const parts = [`p=${grid.columns}x${grid.rows}${order}`];
  if (grid.gapH || grid.gapV) {
    parts.push(Math.abs(grid.gapH - grid.gapV) < 1e-9
      ? `g=${format(grid.gapH)}`
      : `g=[${format(grid.gapH)} ${format(grid.gapV)}]`);
  }
  if (grid.margins.some(Boolean)) parts.push(`b=${compact(grid.margins)}`);
  return `.Layout{${parts.join(' ')}}`;
}

export function spriteReference(model, cell) {
  const alias = String(model.spriteAlias || 'sprites').trim().replace(/^@/, '') || 'sprites';
  return cell ? `@${alias}[${cell.address}]` : `@${alias}[*]`;
}

export function spritesheetDefinition(model, grid, sourceName) {
  const alias = String(model.spriteAlias || 'sprites').trim().replace(/^@/, '') || 'sprites';
  const source = String(sourceName || 'spritesheet.png').trim();
  const layout = layoutExpression(grid, model);
  return layout ? `# @${alias} = @{${source}}${layout}` : '';
}
