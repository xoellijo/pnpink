declare module 'lil-xgui' {
  export class XGUI {
    mode: string;
    domElement: HTMLElement;
    $multi?: HTMLElement;
    constructor(options?: Record<string, unknown>);
    add(target: Record<string, unknown>, property: string, ...options: unknown[]): XGUI;
    append(target: Record<string, unknown>, property: string, ...options: unknown[]): XGUI;
    addFolder(title: string, options?: Record<string, unknown>): XGUI;
    name(value: string): XGUI;
    icon(value: string, size?: string, color?: string): XGUI;
    place(x: number, y: number): void;
    hide(): void;
    destroy(): void;
  }
}

declare module 'pnpink-boardzilla-store' {
  interface BoardElement {
    branch(): string;
  }

  interface BoardMove {
    name: string;
  }

  interface StoreState {
    move?: BoardMove;
    pendingMoves?: BoardMove[];
    selected?: BoardElement[];
    dragOffset: {
      ref?: number;
      x?: number;
      y?: number;
    };
    uncommittedArgs: Record<string, unknown>;
    boardSelections: Record<string, {
      clickMoves: BoardMove[];
      dragMoves: Array<{ move: BoardMove }>;
    }>;
    gameManager: {
      game: { atBranch(branch: string): BoardElement | undefined };
    };
    selectElement(moves: BoardMove[], element: BoardElement): void;
    selectMove(move?: BoardMove, args?: Record<string, unknown>): void;
    clearMove(): void;
  }

  export const gameStore: {
    <T>(selector: (state: StoreState) => T): T;
    getState(): StoreState;
    setState(state: Partial<StoreState>): void;
  };
}
