import {
  computeSpriteGrid,
  spritesheetDefinition,
} from './spritesheet-model.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
export class SpritesheetEditor {
  constructor(elements, model, presets, previewViewer, notify, request, resourceUrl, onUpdate = () => {}) {
    this.elements = elements;
    this.model = model;
    this.presets = presets || {};
    this.previewViewer = previewViewer;
    this.notify = notify;
    this.request = request;
    this.resourceUrl = resourceUrl;
    this.onUpdate = onUpdate;
    this.metadata = null;
    this.grid = null;
    this.sourceName = '';
    this.objectUrl = '';
    this.active = false;
    this.pdf = null;
    this.updateFrame = 0;
    this.elements.image.addEventListener('load', () => this.onImageLoad());
    this.elements.pdfPrevious.addEventListener('click', () => this.changePdfPage(-1));
    this.elements.pdfNext.addEventListener('click', () => this.changePdfPage(1));
    this.elements.pdfSelect.addEventListener('change', () => {
      this.selectPdfPage(Number(this.elements.pdfSelect.value));
    });
  }

  setPresets(presets) { this.presets = presets || {}; }
  setMetadata(metadata) { this.svgMetadata = metadata || null; }

  sourceDescription() {
    if (!this.sourceName) return 'Drop an image or PDF on the canvas to inspect it as a spritesheet.';
    if (!this.metadata) return this.sourceName;
    if (this.pdf) return `${this.sourceName} · PDF page ${this.pdf.page}${this.pdf.pageCount ? `/${this.pdf.pageCount}` : ''}`;
    return `${this.sourceName}\n${this.metadata.widthPx} × ${this.metadata.heightPx} px`;
  }

  async loadFile(file) {
    const pdfFile = file && (
      String(file.type || '').toLowerCase() === 'application/pdf'
      || /\.pdf$/i.test(String(file.name || ''))
    );
    if (pdfFile) return this.loadPdf(file);
    const imageFile = file && (
      String(file.type || '').startsWith('image/')
      || /\.(?:png|jpe?g|webp|gif|svg|bmp|avif)$/i.test(String(file.name || ''))
    );
    if (!imageFile) {
      this.notify('Drop a PNG, JPEG, WebP, GIF, SVG or PDF file');
      return false;
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = URL.createObjectURL(file);
    this.sourceName = file.name || 'spritesheet.png';
    this.metadata = null;
    this.pdf = null;
    this.updatePdfControls();
    this.active = true;
    this.model.spriteSource = this.sourceName;
    this.elements.overlay.hidden = false;
    this.previewViewer.showExternalImage(this.objectUrl, this.model.spriteDpi);
    this.onUpdate();
    return true;
  }

  async loadPdf(file) {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = '';
    this.previewViewer.showMessage('Rendering PDF page 1 with Inkscape...');
    try {
      const filename = file.name || 'spritesheet.pdf';
      let data = await this.request('/api/spritesheet/pdf-project', {
        method: 'POST',
        body: JSON.stringify({ filename }),
      });
      if (!data.found) {
        if (Number(file.size || 0) > 100 * 1024 * 1024) {
          throw new Error('The PDF exceeds the 100 MB upload limit. Place it beside the SVG template or under assets/.');
        }
        data = await this.request('/api/spritesheet/pdf', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/pdf',
            'X-DeckMaker-Filename': encodeURIComponent(filename),
          },
          body: file,
        });
      }
      this.sourceName = data.sourcePath || file.name || 'spritesheet.pdf';
      this.metadata = null;
      this.active = true;
      this.model.spriteSource = this.sourceName;
      this.elements.overlay.hidden = false;
      this.showPdfPage(data);
      return true;
    } catch (error) {
      this.previewViewer.showMessage(error.message || 'The PDF page could not be rendered.');
      this.notify(error.message || 'The PDF page could not be rendered');
      return false;
    }
  }

  async changePdfPage(delta) {
    if (!this.pdf) return;
    return this.selectPdfPage(this.pdf.page + delta);
  }

  async selectPdfPage(target) {
    if (!this.pdf || target < 1 || target === this.pdf.page) return;
    this.elements.pdfPrevious.disabled = true;
    this.elements.pdfNext.disabled = true;
    this.elements.pdfSelect.disabled = true;
    this.previewViewer.showMessage(`Rendering PDF page ${target} with Inkscape...`);
    try {
      const data = await this.request('/api/spritesheet/pdf-page', {
        method: 'POST',
        body: JSON.stringify({ page: target }),
      });
      this.metadata = null;
      this.showPdfPage(data);
    } catch (error) {
      this.previewViewer.hideMessage();
      this.notify(error.message || `PDF page ${target} is not available`);
      this.updatePdfControls();
    }
  }

  showPdfPage(data) {
    this.pdf = data;
    this.sourceName = data.sourcePath || this.sourceName;
    this.model.spriteSource = this.sourceName;
    this.model.spriteDpi = Number(data.dpi || 96);
    this.updatePdfControls();
    this.previewViewer.showExternalImage(this.resourceUrl(data.previewUrl), this.model.spriteDpi);
    this.onUpdate();
  }

  updatePdfControls() {
    const active = Boolean(this.pdf);
    this.elements.pdfControls.hidden = !active;
    if (!active) return;
    this.elements.pdfPrevious.disabled = !this.pdf.hasPrevious;
    this.elements.pdfNext.disabled = !this.pdf.hasNext;
    const pageCount = Math.max(1, Number(this.pdf.pageCount || this.pdf.page || 1));
    const signature = String(pageCount);
    if (this.elements.pdfSelect.dataset.signature !== signature) {
      this.elements.pdfSelect.replaceChildren(...Array.from({ length: pageCount }, (_, index) => {
        const option = document.createElement('option');
        option.value = String(index + 1);
        option.textContent = `${index + 1}/${pageCount}`;
        return option;
      }));
      this.elements.pdfSelect.dataset.signature = signature;
    }
    this.elements.pdfSelect.value = String(this.pdf.page);
    this.elements.pdfSelect.disabled = false;
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
    this.metadata = null;
    this.pdf = null;
    this.updatePdfControls();
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
    if (this.grid && String(this.model.spriteMode || 'Grid').toLowerCase() !== 'grid') {
      this.model.spriteCols = this.grid.columns;
      this.model.spriteRows = this.grid.rows;
    }
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
      group.append(rectangle);
      overlay.appendChild(group);
    }
    this.previewViewer.apply();
  }

  definition() { return spritesheetDefinition(this.model, this.grid, this.sourceName); }

  async copy(value) {
    try {
      await navigator.clipboard.writeText(String(value || ''));
      this.notify('Copied to clipboard');
    } catch {
      this.notify('Clipboard access was denied');
    }
  }

  copyDefinition() { return this.copy(this.definition()); }
}
