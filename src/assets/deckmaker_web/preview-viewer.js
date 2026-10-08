export class PreviewViewer {
  constructor(elements, token = '') {
    this.elements = elements;
    this.token = token;
    this.scale = 1;
    this.angle = 0;
    this.panX = 0;
    this.panY = 0;
    this.revision = -1;
    this.quality = '';
    this.externalDpi = 0;
    this.fitOnLoad = true;
    this.previousNaturalWidth = 0;
    this.previousNaturalHeight = 0;
    this.drag = null;
    this.overlays = Array.from(elements.overlays || []);

    elements.image.addEventListener('load', () => {
      elements.stage.style.width = `${elements.image.naturalWidth}px`;
      elements.stage.style.height = `${elements.image.naturalHeight}px`;
      if (this.fitOnLoad) this.fit();
      else {
        if (this.previousNaturalWidth && this.previousNaturalHeight
            && elements.image.naturalWidth && elements.image.naturalHeight) {
          const widthRatio = this.previousNaturalWidth / elements.image.naturalWidth;
          const heightRatio = this.previousNaturalHeight / elements.image.naturalHeight;
          this.scale *= Math.min(widthRatio, heightRatio);
        }
        this.apply();
      }
      this.fitOnLoad = false;
      this.previousNaturalWidth = 0;
      this.previousNaturalHeight = 0;
    });
    elements.image.addEventListener('error', () => this.showMessage('SVG preview could not be displayed.'));
    elements.viewport.addEventListener('wheel', event => this.onWheel(event), { passive: false });
    elements.viewport.addEventListener('pointerdown', event => this.onPointerDown(event));
    elements.viewport.addEventListener('pointermove', event => this.onPointerMove(event));
    elements.viewport.addEventListener('pointerup', event => this.onPointerUp(event));
    elements.viewport.addEventListener('pointercancel', event => this.onPointerUp(event));
    elements.viewport.addEventListener('keydown', event => this.onKeyDown(event));
    new ResizeObserver(() => {
      if (!elements.image.hidden && !this.drag) this.apply();
    }).observe(elements.viewport);
  }

  setPreview(preview) {
    this.externalDpi = 0;
    const available = Boolean(preview?.available);
    if (!available) {
      this.revision = Number(preview?.revision ?? -1);
      this.elements.image.hidden = true;
      this.elements.image.removeAttribute('src');
      this.showMessage(preview?.message || 'Choose an asset to render its preview.');
      return;
    }
    const revision = Number(preview.revision || 0);
    if (revision === this.revision && this.elements.image.src) return;
    this.revision = revision;
    const quality = String(preview.quality || 'high');
    this.fitOnLoad = !this.elements.image.src || !this.elements.image.naturalWidth;
    this.previousNaturalWidth = this.fitOnLoad ? 0 : this.elements.image.naturalWidth;
    this.previousNaturalHeight = this.fitOnLoad ? 0 : this.elements.image.naturalHeight;
    this.quality = quality;
    const separator = String(preview.url || '').includes('?') ? '&' : '?';
    this.elements.image.src = `${preview.url}${separator}token=${encodeURIComponent(this.token)}`;
    this.elements.image.hidden = false;
    this.elements.message.hidden = true;
  }

  showExternalImage(url, dpi = 96) {
    this.revision = -1;
    this.quality = 'draft';
    this.externalDpi = Math.max(1, Number(dpi || 96));
    this.fitOnLoad = true;
    this.previousNaturalWidth = 0;
    this.previousNaturalHeight = 0;
    this.elements.image.src = String(url || '');
    this.elements.image.hidden = false;
    this.elements.message.hidden = true;
  }

  setExternalDpi(dpi) {
    this.externalDpi = Math.max(1, Number(dpi || 96));
    this.updateGrid();
  }

  showMessage(text) {
    this.elements.message.textContent = text;
    this.elements.message.hidden = false;
  }

  apply() {
    const transform = `translate(-50%, -50%) translate(${this.panX}px, ${this.panY}px) rotate(${this.angle}deg) scale(${this.scale})`;
    this.elements.stage.style.transform = transform;
    this.updateGrid();
  }

  updateGrid() {
    const viewport = this.elements.viewport;
    const image = this.elements.image;
    const scaleElement = this.elements.scale;
    if (!viewport || !scaleElement || image.hidden || !image.naturalWidth) {
      if (scaleElement) scaleElement.hidden = true;
      return;
    }
    const dpi = this.externalDpi || (this.quality === 'draft' ? 96 : 600);
    const pixelsPerMm = dpi * this.scale / 25.4;
    const intervals = [1, 2, 5, 10, 20, 50, 100, 200, 500];
    const interval = intervals.find(value => value * pixelsPerMm >= 34) || intervals.at(-1);
    const size = Math.max(8, interval * pixelsPerMm);
    const originX = viewport.clientWidth / 2 + this.panX - image.naturalWidth * this.scale / 2;
    const originY = viewport.clientHeight / 2 + this.panY - image.naturalHeight * this.scale / 2;
    viewport.style.setProperty('--preview-grid-size', `${size}px`);
    viewport.style.setProperty('--preview-grid-x', `${originX}px`);
    viewport.style.setProperty('--preview-grid-y', `${originY}px`);
    scaleElement.style.setProperty('--preview-scale-width', `${size}px`);
    scaleElement.querySelector('span').textContent = `${interval} mm`;
    scaleElement.hidden = false;
  }

  fit() {
    const image = this.elements.image;
    const viewport = this.elements.viewport;
    if (image.hidden || !image.naturalWidth || !image.naturalHeight) return;
    const radians = Math.abs(this.angle % 180) * Math.PI / 180;
    const width = Math.abs(image.naturalWidth * Math.cos(radians)) + Math.abs(image.naturalHeight * Math.sin(radians));
    const height = Math.abs(image.naturalWidth * Math.sin(radians)) + Math.abs(image.naturalHeight * Math.cos(radians));
    this.scale = Math.min((viewport.clientWidth - 36) / width, (viewport.clientHeight - 36) / height);
    this.scale = Math.max(0.01, Math.min(this.scale, 50));
    this.panX = 0;
    this.panY = 0;
    this.apply();
  }

  zoom(factor, clientX, clientY) {
    const viewport = this.elements.viewport;
    const oldScale = this.scale;
    this.scale = Math.max(0.01, Math.min(oldScale * factor, 50));
    if (Number.isFinite(clientX) && Number.isFinite(clientY)) {
      const rect = viewport.getBoundingClientRect();
      const x = clientX - rect.left - rect.width / 2;
      const y = clientY - rect.top - rect.height / 2;
      const ratio = this.scale / oldScale;
      this.panX = x - (x - this.panX) * ratio;
      this.panY = y - (y - this.panY) * ratio;
    }
    this.apply();
  }

  rotate(delta) {
    this.angle = (this.angle + delta) % 360;
    this.apply();
  }

  onWheel(event) {
    event.preventDefault();
    if (event.shiftKey) {
      this.rotate(event.deltaY < 0 ? -5 : 5);
      return;
    }
    this.zoom(Math.exp(-event.deltaY * 0.0015), event.clientX, event.clientY);
  }

  onPointerDown(event) {
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    this.elements.viewport.focus({ preventScroll: true });
    this.elements.viewport.setPointerCapture(event.pointerId);
    this.drag = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: this.panX,
      panY: this.panY,
      angle: this.angle,
      rotate: event.shiftKey,
    };
    this.elements.viewport.classList.add('dragging');
  }

  onPointerMove(event) {
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - this.drag.x;
    const dy = event.clientY - this.drag.y;
    if (this.drag.rotate) this.angle = this.drag.angle + dx * 0.3;
    else {
      this.panX = this.drag.panX + dx;
      this.panY = this.drag.panY + dy;
    }
    this.apply();
  }

  onPointerUp(event) {
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    this.drag = null;
    this.elements.viewport.classList.remove('dragging');
  }

  onKeyDown(event) {
    if (event.key === '+' || event.key === '=') this.zoom(1.2);
    else if (event.key === '-') this.zoom(1 / 1.2);
    else if (event.key === '5') this.fit();
    else if (event.key === '0') {
      this.scale = 1;
      this.angle = 0;
      this.panX = 0;
      this.panY = 0;
      this.apply();
    } else return;
    event.preventDefault();
  }
}
