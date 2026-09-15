import React, { useEffect } from 'react';

export interface HoverShortcuts {
  flip?: () => void;
  selectTop?: (count: number) => void;
  shuffle?: () => void;
}

const registered = new Map<string, HoverShortcuts>();
let pointer = { x: 0, y: 0 };

function shortcutsAtPointer(): HoverShortcuts | undefined {
  const hovered = Array.from(document.querySelectorAll(':hover')).reverse();
  for (const node of hovered) {
    const branch = (node as HTMLElement).dataset?.pnpinkBranch;
    if (branch && registered.has(branch)) return registered.get(branch);
  }
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

export function useHoverShortcuts(branch: string, shortcuts: HoverShortcuts): void {
  useEffect(() => {
    registered.set(branch, shortcuts);
    return () => { registered.delete(branch); };
  }, [branch, shortcuts]);
}

export function KeyboardShortcuts() {
  useEffect(() => {
    const keyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isElement = typeof target?.matches === 'function';
      if (event.repeat || isElement && (target.matches('input, textarea, select') || target.isContentEditable)) return;
      const hovered = shortcutsAtPointer();
      if (!hovered) return;
      if (isElement && target.closest('.lil-gui, button')) target.blur();
      if (event.key.toLowerCase() === 'f' && hovered.flip) {
        event.preventDefault();
        hovered.flip();
        return;
      }
      if (event.key.toLowerCase() === 's' && hovered.shuffle) {
        event.preventDefault();
        hovered.shuffle();
        return;
      }
      if (/^[0-9]$/.test(event.key) && hovered.selectTop) {
        event.preventDefault();
        hovered.selectTop(event.key === '0' ? 0 : event.key === '1' ? 10 : Number(event.key));
      }
    };
    const trackPointer = (event: PointerEvent) => {
      pointer = { x: event.clientX, y: event.clientY };
    };
    window.addEventListener('pointermove', trackPointer, { passive: true });
    window.addEventListener('pointerover', trackPointer, { passive: true });
    window.addEventListener('keydown', keyDown, true);
    if (window.parent !== window) window.parent.addEventListener('keydown', keyDown, true);
    return () => {
      window.removeEventListener('pointermove', trackPointer);
      window.removeEventListener('pointerover', trackPointer);
      window.removeEventListener('keydown', keyDown, true);
      if (window.parent !== window) window.parent.removeEventListener('keydown', keyDown, true);
    };
  }, []);
  return null;
}
