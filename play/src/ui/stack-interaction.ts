import { Piece } from '@boardzilla/core';
import { areaItems, isPlayItem, manifest, PlayArea, PlayItem } from '../game/index.js';

export type StackSelectionMode = 'top' | 'bottom' | 'random';

export interface StackSelectionPreference {
  digit: number;
  plusTen: boolean;
  mode: StackSelectionMode;
  all: boolean;
}

export interface StackVisualState {
  items: PlayItem[];
  remainingItems: PlayItem[];
  selectedItems: PlayItem[];
  totalTabHost: boolean;
  selectedTabHost: boolean;
  allSelected: boolean;
}

export interface StackTabOptions {
  showCount: boolean;
  autoHide: boolean;
}

export class StackInteractionController {
  private readonly preferences = new Map<string, StackSelectionPreference>();

  constructor(
    private readonly readSelection: () => unknown[],
    private readonly writeSelection: (items: PlayItem[]) => void,
  ) {}

  isStack(zone: PlayArea): boolean {
    const definition = manifest.areas.find(candidate => candidate.id === zone.definitionId);
    const rows = definition?.layout.rows ?? 1;
    const columns = definition?.layout.columns ?? 1;
    return areaItems(zone).length > 1 && rows * columns === 1;
  }

  tabOptions(zone: PlayArea): StackTabOptions {
    const options = manifest.areas.find(candidate => candidate.id === zone.definitionId)?.stackTab;
    return {
      showCount: options?.showCount ?? true,
      autoHide: options?.autoHide ?? true,
    };
  }

  top(zone: PlayArea, count = areaItems(zone).length): PlayItem[] {
    return [...zone.topN(count, Piece)].filter(isPlayItem);
  }

  bottom(zone: PlayArea, count = areaItems(zone).length): PlayItem[] {
    return [...zone.bottomN(count, Piece)].filter(isPlayItem);
  }

  selectCount(zone: PlayArea, count: number): void {
    const preference: StackSelectionPreference = count
      ? {
        digit: count >= 10 ? Math.min(9, count - 10) : count,
        plusTen: count >= 10,
        mode: 'top',
        all: false,
      }
      : { digit: 1, plusTen: false, mode: 'top', all: true };
    this.preferences.set(zone.branch(), preference);
    this.apply(zone, preference);
  }

  selection(zone: PlayArea): StackSelectionPreference {
    const items = areaItems(zone);
    const selected = this.selectedIn(zone);
    const key = zone.branch();
    if (!selected.length) {
      const preference: StackSelectionPreference = { digit: 1, plusTen: false, mode: 'top', all: false };
      this.preferences.set(key, preference);
      return preference;
    }
    const count = selected.length;
    const previous = this.preferences.get(key);
    const detectedMode: StackSelectionMode = this.sameItems(selected, this.top(zone, count))
      ? 'top'
      : this.sameItems(selected, this.bottom(zone, count)) ? 'bottom' : 'random';
    const preference: StackSelectionPreference = {
      digit: count >= 10 ? Math.min(9, count - 10) : Math.min(9, count),
      plusTen: count >= 10,
      mode: count === items.length && previous ? previous.mode : detectedMode,
      all: count === items.length,
    };
    this.preferences.set(key, preference);
    return preference;
  }

  update(zone: PlayArea, changes: Partial<StackSelectionPreference>): StackSelectionPreference {
    const preference = { ...this.selection(zone), ...changes };
    this.preferences.set(zone.branch(), preference);
    this.apply(zone, preference);
    return preference;
  }

  visualState(zone: PlayArea, piece: PlayItem, selectedPieces: PlayItem[]): StackVisualState {
    const items = this.isStack(zone) ? this.top(zone) : [];
    const selectedInZone = selectedPieces.filter(candidate => piece.game.zoneOf(candidate)?.branch() === zone.branch());
    const selectionActive = selectedInZone.length > 0 && selectedInZone.length === selectedPieces.length;
    const selectedItems = selectionActive ? selectedInZone : [];
    const selectedBranches = new Set(selectedItems.map(candidate => candidate.branch()));
    const remainingItems = selectionActive
      ? items.filter(candidate => !selectedBranches.has(candidate.branch()))
      : items;
    const totalTabItem = remainingItems[0];
    const selectedTabItem = items.find(candidate => selectedBranches.has(candidate.branch()));
    return {
      items,
      remainingItems,
      selectedItems,
      totalTabHost: items.length > 1 && Boolean(totalTabItem) && totalTabItem?.branch() === piece.branch(),
      selectedTabHost: selectedItems.length > 1 && selectedTabItem?.branch() === piece.branch(),
      allSelected: items.length > 1 && selectedItems.length === items.length,
    };
  }

  private selectedIn(zone: PlayArea): PlayItem[] {
    return this.readSelection().filter((candidate): candidate is PlayItem => (
      isPlayItem(candidate) && candidate.container() === zone
    ));
  }

  private apply(zone: PlayArea, preference: StackSelectionPreference): void {
    const items = areaItems(zone);
    if (preference.all) {
      this.writeSelection(items);
      return;
    }
    const count = Math.min(items.length, preference.digit + (preference.plusTen ? 10 : 0));
    if (preference.mode === 'bottom') this.writeSelection(this.bottom(zone, count));
    else if (preference.mode === 'random') this.writeSelection(this.shuffled(items).slice(0, count));
    else this.writeSelection(this.top(zone, count));
  }

  private shuffled(items: PlayItem[]): PlayItem[] {
    const shuffled = [...items];
    for (let index = shuffled.length - 1; index > 0; index -= 1) {
      const target = Math.floor(Math.random() * (index + 1));
      [shuffled[index], shuffled[target]] = [shuffled[target], shuffled[index]];
    }
    return shuffled;
  }

  private sameItems(left: PlayItem[], right: PlayItem[]): boolean {
    if (left.length !== right.length) return false;
    const branches = new Set(left.map(item => item.branch()));
    return right.every(item => branches.has(item.branch()));
  }
}
