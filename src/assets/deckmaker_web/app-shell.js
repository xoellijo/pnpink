class AppShell {
  constructor() {
    this.installEvent = null;
    this.listeners = new Set();
    this.launchedAsApp = new URLSearchParams(location.search).get('shell') === 'app';
    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      this.installEvent = event;
      this.notify();
    });
    window.addEventListener('appinstalled', () => {
      this.installEvent = null;
      this.notify();
    });
    document.addEventListener('fullscreenchange', () => this.notify());
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('./service-worker.js').catch(() => {});
    }
  }

  get standalone() {
    return this.launchedAsApp
      || window.matchMedia('(display-mode: standalone)').matches
      || window.matchMedia('(display-mode: fullscreen)').matches
      || Boolean(navigator.standalone);
  }

  get canInstall() {
    return !this.standalone && Boolean(this.installEvent);
  }

  get fullscreen() {
    return Boolean(document.fullscreenElement);
  }

  subscribe(listener) {
    this.listeners.add(listener);
    listener(this);
    return () => this.listeners.delete(listener);
  }

  notify() {
    this.listeners.forEach(listener => listener(this));
  }

  async install() {
    if (!this.installEvent) return false;
    const event = this.installEvent;
    this.installEvent = null;
    await event.prompt();
    const result = await event.userChoice;
    this.notify();
    return result.outcome === 'accepted';
  }

  async toggleFullscreen() {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  }
}

export const appShell = new AppShell();
