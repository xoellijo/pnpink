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

function emitPreview(item?: PreviewItem): void {
  window.dispatchEvent(new CustomEvent(previewEvent, { detail: item }));
}

export function setPreviewCandidate(item?: PreviewItem): void {
  candidate = item;
  if (spaceHeld && item) emitPreview(item);
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
      const target = event.target as HTMLElement;
      if (event.code !== 'Space' || event.repeat || target.matches('input, textarea, select')) return;
      if (!candidate) return;
      event.preventDefault();
      if (target.closest('.lil-gui')) target.blur();
      spaceHeld = true;
      emitPreview(candidate);
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
    window.addEventListener(previewEvent, update);
    window.addEventListener('keydown', keyDown);
    window.addEventListener('keydown', escape);
    window.addEventListener('keyup', keyUp);
    return () => {
      window.removeEventListener(previewEvent, update);
      window.removeEventListener('keydown', keyDown);
      window.removeEventListener('keydown', escape);
      window.removeEventListener('keyup', keyUp);
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
