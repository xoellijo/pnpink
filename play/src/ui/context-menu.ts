import type { GameElement } from '@boardzilla/core';
import { XGUI } from 'lil-xgui';
import { gameStore } from 'pnpink-boardzilla-store';

export interface ContextAction {
  label: string;
  icon?: string;
  active?: boolean;
  iconOnly?: boolean;
  run?: () => void;
  children?: ContextAction[];
  row?: ContextAction[];
  compactRow?: boolean;
}

export interface ContextMenuRequest {
  title: string;
  x: number;
  y: number;
  actions: ContextAction[];
}

class ContextMenu {
  private gui?: XGUI;

  constructor() {
    window.addEventListener('pointerdown', event => {
      if (!this.gui || this.gui.domElement.contains(event.target as Node)) return;
      if (event.button === 0) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
      this.hide();
    }, true);
    window.addEventListener('keydown', event => {
      if (event.key === 'Escape') this.hide();
    });
  }

  show(request: ContextMenuRequest): void {
    this.hide();
    const width = Math.min(238, window.innerWidth - 10);
    this.gui = new XGUI({ autoPlace: false, title: '', touchStyles: true, width });
    this.gui.domElement.classList.add('pnpink-context-menu');
    this.gui.domElement.querySelector(':scope > .title')?.remove();
    this.addActions(this.gui, request.actions);
    this.gui.place(request.x, request.y);
    const box = this.gui.domElement.getBoundingClientRect();
    this.gui.domElement.style.position = 'fixed';
    let left = Math.max(5, Math.min(request.x, window.innerWidth - box.width - 5));
    let top = Math.max(5, Math.min(request.y, window.innerHeight - box.height - 5));
    const serverMenu = this.serverMenuRect();
    if (serverMenu && left < serverMenu.right + 5 && left + box.width > serverMenu.left - 5
      && top < serverMenu.bottom + 5 && top + box.height > serverMenu.top - 5) {
      const beside = serverMenu.left - box.width - 6;
      if (beside >= 5) left = beside;
      else top = Math.min(window.innerHeight - box.height - 5, serverMenu.bottom + 6);
    }
    this.gui.domElement.style.left = `${left}px`;
    this.gui.domElement.style.top = `${top}px`;
  }

  private serverMenuRect(): DOMRect | undefined {
    try {
      return window.parent === window
        ? undefined
        : window.parent.document.querySelector<HTMLElement>('.server-menu')?.getBoundingClientRect();
    } catch {
      return undefined;
    }
  }

  hide(gui = this.gui): void {
    if (!gui) return;
    if (this.gui === gui) this.gui = undefined;
    if (gui?.domElement.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    try {
      gui.destroy();
    } catch {
      gui.domElement.remove();
    }
  }

  private run(action?: () => void): void {
    const gui = this.gui;
    action?.();
    queueMicrotask(() => {
      if (this.gui === gui) this.hide(gui);
    });
  }

  private addActions(gui: XGUI, actions: ContextAction[]): void {
    for (const [index, action] of actions.entries()) {
      if (action.row?.length) {
        const state: Record<string, () => void> = {};
        let first: XGUI | undefined;
        for (const [rowIndex, rowAction] of action.row.entries()) {
          const key = `action${index}_${rowIndex}`;
          state[key] = () => {
            if (!rowAction.run) return;
            this.run(rowAction.run);
          };
          const controller = first ? first.append(state, key) : gui.add(state, key);
          controller.name(rowAction.iconOnly ? '' : rowAction.label);
          controller.domElement.title = rowAction.label;
          controller.domElement.setAttribute('aria-label', rowAction.label);
          if (rowAction.iconOnly) controller.domElement.classList.add('pnpink-icon-only');
          if (rowAction.icon) controller.icon(rowAction.icon);
          if (rowAction.active) controller.domElement.classList.add('pnpink-active');
          first ??= controller;
        }
        const row = first?.$multi ?? first?.domElement;
        row?.classList.add('pnpink-context-inline');
        if (action.compactRow) row?.classList.add('pnpink-context-compact');
        continue;
      }
      if (action.children?.length) {
        const folder = gui.addFolder(action.label);
        if (action.icon) folder.icon(action.icon);
        this.addActions(folder, action.children);
        continue;
      }
      const key = `action${index}`;
      const controller = gui.add({ [key]: () => {
        this.run(action.run);
      } }, key).name(action.label);
      controller.domElement.title = action.label;
      controller.domElement.setAttribute('aria-label', action.label);
      if (action.icon) controller.icon(action.icon);
      if (action.active) controller.domElement.classList.add('pnpink-active');
    }
  }
}

export const contextMenu = new ContextMenu();

function clearTransientDragVisuals(): void {
  const { dragOffset } = gameStore.getState();
  dragOffset.ref = undefined;
  dragOffset.x = undefined;
  dragOffset.y = undefined;
  document.querySelectorAll<HTMLElement>('.group-dragging').forEach(node => {
    node.classList.remove('group-dragging');
    node.style.removeProperty('translate');
  });
}

export function canChoose(element: GameElement, actionName: string): boolean {
  return choicesFor(element, actionName).length > 0;
}

export function useCanChoose(element: GameElement, actionName: string): boolean {
  return gameStore(state => choicesForState(state, element, actionName).length > 0);
}

export function chooseElement(element: GameElement, actionName: string): boolean {
  const choices = choicesFor(element, actionName);
  if (!choices.length) return false;
  clearTransientDragVisuals();
  gameStore.getState().selectElement(choices, element);
  return true;
}

export function chooseSequence(actionName: string, ...elements: GameElement[]): boolean {
  for (const element of elements) {
    if (!chooseElement(element, actionName)) return false;
  }
  gameStore.setState({ selected: undefined });
  return true;
}

export function beginChoice(actionName: string, args: Record<string, unknown>): boolean {
  const state = gameStore.getState();
  const move = state.pendingMoves?.find(candidate => candidate.name === actionName);
  if (!move) return false;
  clearTransientDragVisuals();
  state.clearMove();
  gameStore.getState().selectMove(move, args);
  return true;
}

export function submitChoice(actionName: string, args: Record<string, unknown>): boolean {
  const state = gameStore.getState();
  const move = state.pendingMoves?.find(candidate => candidate.name === actionName);
  if (!move) return false;
  clearTransientDragVisuals();
  state.clearMove();
  gameStore.getState().selectMove(move, args);
  gameStore.setState({ selected: undefined });
  return true;
}

export function hasPendingAction(actionName: string): boolean {
  return Boolean(gameStore.getState().pendingMoves?.some(candidate => candidate.name === actionName));
}

function choicesFor(element: GameElement, actionName: string) {
  return choicesForState(gameStore.getState(), element, actionName);
}

function choicesForState(state: ReturnType<typeof gameStore.getState>, element: GameElement, actionName: string) {
  const selections = state.boardSelections[element.branch()];
  if (!selections) return [];
  return [...new Set([
    ...selections.clickMoves,
    ...selections.dragMoves.map(selection => selection.move),
  ])].filter(move => move.name === actionName);
}
