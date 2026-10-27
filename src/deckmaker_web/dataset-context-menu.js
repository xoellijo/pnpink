import { XGUI } from './vendor/lil-xgui.min.js';
import { menuIcon } from './icon-set.js';

export class DatasetContextMenu {
  constructor(actions = []) {
    this.context = null;
    this.sequence = 0;
    this.gui = new XGUI({
      autoPlace: false,
      title: '',
      width: 220,
    });
    this.gui.domElement.classList.add('deckmaker-menu', 'dataset-context-menu');
    this.gui.$title.hidden = true;
    this.entries = this.build(this.gui, actions);
    this.gui.hide();

    this.handleOutsidePointer = event => {
      if (!this.gui.domElement.contains(event.target)) this.close();
    };
    this.handleKeyDown = event => {
      if (event.key === 'Escape') this.close();
    };
    this.handleViewportChange = event => {
      if (!this.gui.domElement.contains(event.target)) this.close();
    };
    document.addEventListener('pointerdown', this.handleOutsidePointer, true);
    document.addEventListener('keydown', this.handleKeyDown, true);
    document.addEventListener('scroll', this.handleViewportChange, true);
    window.addEventListener('resize', this.handleViewportChange);
  }

  build(parent, actions) {
    const entries = [];
    for (const action of actions) {
      if (action.type === 'separator') {
        const element = document.createElement('div');
        element.className = 'dataset-context-separator';
        parent.$children.append(element);
        entries.push({ action, element });
        continue;
      }

      if (action.children?.length) {
        const folder = parent.addFolder(this.label(action));
        if (action.icon) folder.icon(this.icon(action.icon));
        if (action.tooltip) folder.$title.title = action.tooltip;
        folder.close();
        entries.push({
          action,
          element: folder.domElement,
          folder,
          children: this.build(folder, action.children),
        });
        continue;
      }

      const key = `action${this.sequence++}`;
      const holder = {
        [key]: () => {
          const context = this.context;
          this.close();
          action.run?.(context);
        },
      };
      const controller = parent.add(holder, key).name(this.label(action));
      if (action.icon) controller.icon(this.icon(action.icon));
      if (action.tooltip) {
        controller.domElement.title = action.tooltip;
        controller.domElement.setAttribute('aria-label', action.tooltip);
      }
      if (action.danger) controller.domElement.classList.add('dataset-context-danger');
      entries.push({ action, element: controller.domElement, controller });
    }
    return entries;
  }

  icon(name) {
    return menuIcon(name);
  }

  label(action) {
    return typeof action.label === 'function'
      ? action.label(this.context)
      : action.label;
  }

  applies(action, context) {
    if (action.contexts && !action.contexts.includes(context.type)) return false;
    return action.visible ? Boolean(action.visible(context)) : true;
  }

  updateEntry(entry, context) {
    const { action, controller, folder, element } = entry;
    let visible = this.applies(action, context);
    if (entry.children) {
      const visibleChildren = entry.children.map(child => this.updateEntry(child, context));
      visible &&= visibleChildren.some(Boolean);
      folder.close();
    }
    element.style.display = visible ? '' : 'none';
    if (!visible || !controller) return visible;

    controller.name(this.label(action));
    const enabled = action.enabled ? Boolean(action.enabled(context)) : true;
    const button = controller.domElement.querySelector('button');
    if (button) button.disabled = !enabled;
    controller.domElement.classList.toggle('disabled', !enabled);
    return visible;
  }

  open(event, context) {
    event.preventDefault();
    event.stopPropagation();
    this.context = context;
    this.entries.forEach(entry => this.updateEntry(entry, context));
    this.gui.place(event.clientX, event.clientY);
    this.gui.domElement.style.position = 'fixed';
  }

  close() {
    this.context = null;
    this.gui.hide();
  }
}
