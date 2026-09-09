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
      if (this.gui && !this.gui.domElement.contains(event.target as Node)) this.hide();
    });
    window.addEventListener('keydown', event => {
      if (event.key === 'Escape') this.hide();
    });
  }

  show(request: ContextMenuRequest): void {
    this.hide();
    const needsSelectionWidth = request.actions.some(action => (action.row?.length ?? 0) >= 10);
    const width = window.matchMedia('(pointer: coarse)').matches || needsSelectionWidth
      ? Math.min(288, window.innerWidth - 10)
      : 184;
    this.gui = new XGUI({ autoPlace: false, mode: 'contextmenu', title: request.title, touchStyles: true, width });
    this.gui.domElement.classList.add('pnpink-context-menu');
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

  hide(): void {
    if (this.gui?.domElement.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    this.gui?.destroy();
    this.gui = undefined;
  }

  private addActions(gui: XGUI, actions: ContextAction[]): void {
    for (const [index, action] of actions.entries()) {
      if (action.row?.length) {
        const state: Record<string, () => void> = {};
        let first: XGUI | undefined;
        let previous: XGUI | undefined;
        for (const [rowIndex, rowAction] of action.row.entries()) {
          const key = `action${index}_${rowIndex}`;
          state[key] = () => {
            if (!rowAction.run) return;
            rowAction.run?.();
            this.hide();
          };
          const controller = previous ? previous.append(state, key) : gui.add(state, key);
          controller.name(rowAction.iconOnly ? '' : rowAction.label);
          controller.domElement.title = rowAction.label;
          controller.domElement.setAttribute('aria-label', rowAction.label);
          if (rowAction.iconOnly) controller.domElement.classList.add('pnpink-icon-only');
          if (rowAction.icon) controller.icon(rowAction.icon);
          if (rowAction.active) controller.domElement.classList.add('pnpink-active');
          first ??= controller;
          previous = controller;
        }
        (first?.$multi ?? first?.domElement)?.classList.add('pnpink-context-inline');
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
        action.run?.();
        this.hide();
      } }, key).name(action.label);
      if (action.icon) controller.icon(action.icon);
    }
  }
}

export const contextMenu = new ContextMenu();

export function canChoose(element: GameElement, actionName: string): boolean {
  return choicesFor(element, actionName).length > 0;
}

export function chooseElement(element: GameElement, actionName: string): boolean {
  const choices = choicesFor(element, actionName);
  if (!choices.length) return false;
  gameStore.getState().selectElement(choices, element);
  return true;
}

export function chooseSequence(actionName: string, ...elements: GameElement[]): boolean {
  for (const element of elements) {
    if (!chooseElement(element, actionName)) return false;
  }
  return true;
}

export function submitChoice(actionName: string, args: Record<string, unknown>): boolean {
  const state = gameStore.getState();
  const move = state.pendingMoves?.find(candidate => candidate.name === actionName);
  if (!move) return false;
  state.selectMove(move, args);
  return true;
}

export function hasPendingAction(actionName: string): boolean {
  return Boolean(gameStore.getState().pendingMoves?.some(candidate => candidate.name === actionName));
}

function choicesFor(element: GameElement, actionName: string) {
  const state = gameStore.getState();
  return (state.boardSelections[element.branch()]?.clickMoves ?? []).filter(move => move.name === actionName);
}
