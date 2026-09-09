import React, { useEffect } from 'react';

export interface HoverShortcuts {
  flip?: () => void;
  selectTop?: (count: number) => void;
  shuffle?: () => void;
}

const registered = new Map<string, HoverShortcuts>();
let pointer = { x: 0, y: 0 };

export function useHoverShortcuts(branch: string, shortcuts: HoverShortcuts): void {
  useEffect(() => {
    registered.set(branch, shortcuts);
    return () => { registered.delete(branch); };
  }, [branch, shortcuts]);
}

export function KeyboardShortcuts() {
  useEffect(() => {
    const keyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.repeat || target.matches('input, textarea, select') || target.isContentEditable) return;
      const node = document.elementFromPoint(pointer.x, pointer.y)?.closest<HTMLElement>('[data-pnpink-branch]');
      const hovered = node ? registered.get(node.dataset.pnpinkBranch ?? '') : undefined;
      if (!hovered) return;
      if (target.closest('.lil-gui, button')) target.blur();
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
    const trackPointer = (event: PointerEvent) => { pointer = { x: event.clientX, y: event.clientY }; };
    window.addEventListener('pointermove', trackPointer, { passive: true });
    window.addEventListener('pointerover', trackPointer, { passive: true });
    window.addEventListener('keydown', keyDown);
    return () => {
      window.removeEventListener('pointermove', trackPointer);
      window.removeEventListener('pointerover', trackPointer);
      window.removeEventListener('keydown', keyDown);
    };
  }, []);
  return null;
}
