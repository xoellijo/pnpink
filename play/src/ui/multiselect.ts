import type { GameElement } from '@boardzilla/core';
import { gameStore } from 'pnpink-boardzilla-store';

export function selectedElements(): GameElement[] {
  return (gameStore.getState().selected ?? []) as GameElement[];
}

export function useSelectedElements(): GameElement[] {
  return gameStore(state => (state.selected ?? []) as GameElement[]);
}

export function setSelected(elements: GameElement[]): void {
  gameStore.setState({ selected: [...new Set(elements)] });
}

export function clearSelected(): void {
  if (selectedElements().length) gameStore.setState({ selected: undefined });
}

export function toggleSelected(element: GameElement): void {
  const selected = selectedElements();
  setSelected(selected.includes(element)
    ? selected.filter(candidate => candidate !== element)
    : [...selected, element]);
}

export function elementAtBranch(branch: string): GameElement | undefined {
  return gameStore.getState().gameManager.game.atBranch(branch) as GameElement | undefined;
}
