import React, { useEffect, useRef, useState } from 'react';

export interface PreviewItem {
  label: string;
  source?: string;
  node?: HTMLElement;
  content?: React.ReactNode;
}

const previewEvent = 'pnpink-play-preview';
let candidate: PreviewItem | undefined;
let spaceHeld = false;
let pointer = { x: 0, y: 0 };
const registered = new Map<string, PreviewItem>();

function previewAtPointer(): PreviewItem | undefined {
  for (const hit of document.elementsFromPoint(pointer.x, pointer.y)) {
    let node: Element | null = hit;
    while (node) {
      const branch = (node as HTMLElement).dataset?.pnpinkBranch;
      if (branch && registered.has(branch)) return registered.get(branch);
      node = node.parentElement;
    }
  }
  return undefined;
}

function emitPreview(item?: PreviewItem): void {
  window.dispatchEvent(new CustomEvent(previewEvent, { detail: item }));
}

export function setPreviewCandidate(item?: PreviewItem): void {
  candidate = item;
  if (spaceHeld && item) emitPreview(item);
}

export function usePreviewCandidate(branch: string, item?: PreviewItem): void {
  useEffect(() => {
    if (item) registered.set(branch, item);
    else registered.delete(branch);
    return () => { registered.delete(branch); };
  }, [branch, item]);
}

export function openPreview(item: PreviewItem): void {
  emitPreview(item);
}

export function PreviewOverlay() {
  const [item, setItem] = useState<PreviewItem>();
  const content = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const update = (event: Event) => setItem((event as CustomEvent<PreviewItem | undefined>).detail);
    const keyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isElement = typeof target?.matches === 'function';
      if (event.code !== 'Space' || event.repeat || isElement && target.matches('input, textarea, select')) return;
      const item = candidate ?? previewAtPointer();
      if (!item) return;
      event.preventDefault();
      if (isElement && target.closest('.lil-gui')) target.blur();
      spaceHeld = true;
      emitPreview(item);
    };
    const keyUp = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || !spaceHeld) return;
      event.preventDefault();
      spaceHeld = false;
      emitPreview();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') emitPreview();
    };
    const trackPointer = (event: PointerEvent) => {
      pointer = { x: event.clientX, y: event.clientY };
    };
    window.addEventListener(previewEvent, update);
    window.addEventListener('pointermove', trackPointer, { passive: true });
    window.addEventListener('pointerover', trackPointer, { passive: true });
    window.addEventListener('keydown', keyDown, true);
    window.addEventListener('keydown', escape, true);
    window.addEventListener('keyup', keyUp, true);
    if (window.parent !== window) {
      window.parent.addEventListener('keydown', keyDown, true);
      window.parent.addEventListener('keydown', escape, true);
      window.parent.addEventListener('keyup', keyUp, true);
    }
    return () => {
      window.removeEventListener(previewEvent, update);
      window.removeEventListener('pointermove', trackPointer);
      window.removeEventListener('pointerover', trackPointer);
      window.removeEventListener('keydown', keyDown, true);
      window.removeEventListener('keydown', escape, true);
      window.removeEventListener('keyup', keyUp, true);
      if (window.parent !== window) {
        window.parent.removeEventListener('keydown', keyDown, true);
        window.parent.removeEventListener('keydown', escape, true);
        window.parent.removeEventListener('keyup', keyUp, true);
      }
    };
  }, []);

  useEffect(() => {
    const target = content.current;
    if (!target) return;
    target.replaceChildren();
    if (!item?.node) return;
    const clone = item.node.cloneNode(true) as HTMLElement;
    const box = item.node.getBoundingClientRect();
    const scale = Math.min(window.innerWidth * .82 / box.width, window.innerHeight * .78 / box.height);
    clone.removeAttribute('id');
    clone.querySelectorAll<HTMLElement>('[id]').forEach(node => node.removeAttribute('id'));
    clone.style.position = 'relative';
    clone.style.inset = 'auto';
    clone.style.width = `${box.width * scale}px`;
    clone.style.height = `${box.height * scale}px`;
    clone.style.transform = 'none';
    clone.style.translate = 'none';
    clone.style.pointerEvents = 'none';
    target.style.width = clone.style.width;
    target.style.height = clone.style.height;
    target.append(clone);
  }, [item]);

  if (!item) return null;
  return <div className="card-preview-backdrop" onPointerDown={() => emitPreview()}>
    <figure className="card-preview" onPointerDown={event => event.stopPropagation()}>
      {item.source
        ? <img src={item.source} alt={item.label} />
        : item.content
          ? <div className="component-preview">{item.content}</div>
          : <div ref={content} className="component-preview" />}
      <figcaption>{item.label}</figcaption>
    </figure>
  </div>;
}
