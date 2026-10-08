import {
  computeSpriteGrid,
  spriteReference,
  spritesheetDefinition,
} from './spritesheet-model.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
export class SpritesheetEditor {
  constructor(elements, model, presets, previewViewer, notify, onUpdate = () => {}) {
    this.elements = elements;
    this.model = model;
    this.presets = presets || {};
    this.previewViewer = previewViewer;
    this.notify = notify;
    this.onUpdate = onUpdate;
    this.metadata = null;
    this.grid = null;
    this.selectedCell = null;
    this.sourceName = '';
    this.objectUrl = '';
    this.active = false;
    this.updateFrame = 0;
    this.elements.image.addEventListener('load', () => this.onImageLoad());
  }

  setPresets(presets) { this.presets = presets || {}; }
  setMetadata(metadata) { this.svgMetadata = metadata || null; }

  sourceDescription() {
    if (!this.sourceName) return 'Drop an image on the canvas to inspect it as a spritesheet.';
    if (!this.metadata) return this.sourceName;
    return `${this.sourceName}\n${this.metadata.widthPx} × ${this.metadata.heightPx} px`;
  }

  loadFile(file) {
    const imageFile = file && (
      String(file.type || '').startsWith('image/')
      || /\.(?:png|jpe?g|webp|gif|svg|bmp|avif)$/i.test(String(file.name || ''))
    );
    if (!imageFile) {
      this.notify('Drop a PNG, JPEG, WebP, GIF or SVG image');
      return false;
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = URL.createObjectURL(file);
    this.sourceName = file.name || 'spritesheet.png';
    this.selectedCell = null;
    this.metadata = null;
    this.active = true;
    this.model.spriteSource = this.sourceName;
    this.elements.overlay.hidden = false;
    this.previewViewer.showExternalImage(this.objectUrl, this.model.spriteDpi);
    this.onUpdate();
    return true;
  }

  deactivate() {
    if (!this.active) return;
    this.active = false;
    if (this.updateFrame) cancelAnimationFrame(this.updateFrame);
    this.updateFrame = 0;
    this.elements.overlay.replaceChildren();
    this.elements.overlay.hidden = true;
    this.elements.overlay.removeAttribute('viewBox');
    this.elements.overlay.removeAttribute('aria-label');
    this.grid = null;
    this.selectedCell = null;
    this.metadata = null;
  }

  onImageLoad() {
    if (!this.active) return;
    const widthPx = Number(this.elements.image.naturalWidth || 0);
    const heightPx = Number(this.elements.image.naturalHeight || 0);
    if (!widthPx || !heightPx) return;
    this.metadata = {
      available: true,
      nodeId: this.sourceName,
      widthPx,
      heightPx,
    };
    this.updatePhysicalSize();
    this.render();
    this.previewViewer.fit();
  }

  update() {
    if (!this.active) return;
    this.selectedCell = null;
    this.updatePhysicalSize();
    this.render();
  }

  requestUpdate() {
    if (!this.active || this.updateFrame) return;
    this.updateFrame = requestAnimationFrame(() => {
      this.updateFrame = 0;
      this.update();
    });
  }

  updatePhysicalSize() {
    if (!this.metadata) return;
    const dpi = Math.max(1, Number(this.model.spriteDpi || 300));
    this.previewViewer.setExternalDpi(dpi);
    this.metadata.widthMm = this.metadata.widthPx * 25.4 / dpi;
    this.metadata.heightMm = this.metadata.heightPx * 25.4 / dpi;
  }

  render() {
    const overlay = this.elements.overlay;
    overlay.replaceChildren();
    this.grid = computeSpriteGrid(this.model, this.metadata, this.presets);
    this.model.spriteDefinition = spritesheetDefinition(this.model, this.grid, this.sourceName)
      || 'Invalid spritesheet grid';
    this.model.spriteFrame = spriteReference(this.model, this.selectedCell);
    this.onUpdate();
    if (!this.grid || !this.metadata) return;

    overlay.hidden = false;
    overlay.removeAttribute('hidden');
    const widthPx = this.metadata.widthPx;
    const heightPx = this.metadata.heightPx;
    const scaleX = widthPx / this.grid.width;
    const scaleY = heightPx / this.grid.height;
    overlay.style.width = `${widthPx}px`;
    overlay.style.height = `${heightPx}px`;
    overlay.setAttribute('width', widthPx);
    overlay.setAttribute('height', heightPx);
    overlay.setAttribute('preserveAspectRatio', 'none');
    overlay.setAttribute('viewBox', `0 0 ${widthPx} ${heightPx}`);
    overlay.setAttribute('aria-label', `${this.grid.columns} by ${this.grid.rows} spritesheet grid`);
    for (const cell of this.grid.cells) {
      const group = document.createElementNS(SVG_NS, 'g');
      group.classList.add('sprite-cell');
      group.setAttribute('tabindex', '0');
      group.setAttribute('role', 'button');
      group.setAttribute('aria-label', `Frame ${cell.address}, item ${cell.index}`);
      const rectangle = document.createElementNS(SVG_NS, 'rect');
      rectangle.setAttribute('x', cell.x * scaleX);
      rectangle.setAttribute('y', cell.y * scaleY);
      rectangle.setAttribute('width', cell.width * scaleX);
      rectangle.setAttribute('height', cell.height * scaleY);
      rectangle.setAttribute('fill', 'rgba(239, 52, 52, 0.035)');
      rectangle.setAttribute('stroke', '#ef3434');
      rectangle.setAttribute('stroke-width', '1.5');
      rectangle.setAttribute('vector-effect', 'non-scaling-stroke');
      rectangle.style.fill = 'rgba(239, 52, 52, 0.035)';
      rectangle.style.stroke = '#ef3434';
      rectangle.style.strokeWidth = '1.5px';
      rectangle.style.vectorEffect = 'non-scaling-stroke';
      const select = event => {
        event?.stopPropagation();
        this.selectedCell = cell;
        overlay.querySelectorAll('.selected').forEach(node => node.classList.remove('selected'));
        group.classList.add('selected');
        this.model.spriteFrame = spriteReference(this.model, cell);
        this.onUpdate();
      };
      group.addEventListener('click', select);
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') select(event);
      });
      group.append(rectangle);
      overlay.appendChild(group);
    }
    this.previewViewer.apply();
  }

  definition() { return spritesheetDefinition(this.model, this.grid, this.sourceName); }
  frameReference() { return spriteReference(this.model, this.selectedCell); }

  async copy(value) {
    try {
      await navigator.clipboard.writeText(String(value || ''));
      this.notify('Copied to clipboard');
    } catch {
      this.notify('Clipboard access was denied');
    }
  }

  copyDefinition() { return this.copy(this.definition()); }
  copyFrame() { return this.copy(this.frameReference()); }
}
