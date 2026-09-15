const PnPInkPlay = (() => {
  const storageKey = 'pnpinkPlayTokens';
  const legacyKey = ['board', 'zillaLiteTokens'].join('');
  const usersKey = 'pnpinkPlayUsers';
  const activeUserKey = 'pnpinkPlayActiveUser';
  const icons = {
    'arrow-left': '<path d="M21 11.25H5.31l5.22-5.22-1.06-1.06-6.145 6.145a1.26 1.26 0 0 0 0 1.77L9.47 19.03l1.06-1.06-5.22-5.22H21z"/>',
    'arrow-right': '<path d="M20.675 11.115 14.53 4.97l-1.06 1.06 5.22 5.22H3v1.5h15.69l-5.22 5.22 1.06 1.06 6.145-6.145a1.26 1.26 0 0 0 0-1.77"/>',
    'arrow-split-right-alt': '<path fill-rule="evenodd" d="M19.697 5.758 16.97 3.03l1.06-1.06 3.647 3.646a1.25 1.25 0 0 1 0 1.768L18.03 11.03l-1.06-1.06 2.711-2.712-6.87-.008-5.5 5.5H2v-1.5h4.69l5.5-5.5zM9.03 12.97l3.78 3.78h6.88l-2.72-2.72 1.06-1.06 3.647 3.646a1.25 1.25 0 0 1 0 1.768L18.03 22.03l-1.06-1.06 2.72-2.72h-7.5l-4.22-4.22z" clip-rule="evenodd"/>',
    'book-open': '<path d="M15 4a3.75 3.75 0 0 0-3 1.51A3.75 3.75 0 0 0 9 4H2v16h7.5c.69 0 1.25.56 1.25 1.25h2.5c0-.69.56-1.25 1.25-1.25H22V4zm-3.75 15.13a2.73 2.73 0 0 0-1.75-.63h-6v-13H9c1.24 0 2.25 1.01 2.25 2.25zm9.25-.63h-6c-.665 0-1.275.235-1.75.63V7.75c0-1.24 1.01-2.25 2.25-2.25h5.5z"/>',
    'chevron-left': '<path d="m14.97 20.03-7.145-7.145a1.26 1.26 0 0 1 0-1.77L14.97 3.97l1.06 1.06L9.06 12l6.97 6.97z"/>',
    'chevron-right': '<path d="m9.03 20.03-1.06-1.06L14.94 12 7.97 5.03l1.06-1.06 7.145 7.145c.485.485.485 1.28 0 1.77z"/>',
    'check': '<path d="m9.55 18.2-5.4-5.4 1.06-1.06 4.34 4.34 9.24-9.24 1.06 1.06z"/>',
    'close': '<path d="m20.28 4.78-1.06-1.06L12 10.94 4.78 3.72 3.72 4.78 10.94 12l-7.22 7.22 1.06 1.06L12 13.06l7.22 7.22 1.06-1.06L13.06 12z"/>',
    'download': '<path d="M11.25 3h1.5v11.19l3.72-3.72 1.06 1.06L12 17.06l-5.53-5.53 1.06-1.06 3.72 3.72zM4 19h16v1.5H4z"/>',
    'eye': '<path fill-rule="evenodd" d="M2.065 12.31c.13.29 3.275 7.19 9.935 7.19s9.805-6.895 9.935-7.19l.145-.33-.165-.32C21.765 11.365 18.135 4.5 12 4.5c-6.66 0-9.805 6.905-9.935 7.2l-.135.305zM12 18c-4.95 0-7.755-4.735-8.415-5.995C4.245 10.74 7.055 6 12 6c4.525 0 7.635 4.73 8.4 6.025C19.72 13.32 16.95 18 12 18m-4-6c0 2.205 1.795 4 4 4s4-1.795 4-4-1.795-4-4-4-4 1.795-4 4m1.5 0a2.501 2.501 0 0 1 5 0 2.5 2.5 0 0 1-5 0" clip-rule="evenodd"/>',
    'home': '<path d="M19.75 21H14v-5.005a2 2 0 1 0-4 0V21H4.25C3.56 21 3 20.44 3 19.75v-9.315l8.13-7.885a1.24 1.24 0 0 1 1.74 0L21 10.435v9.315c0 .69-.56 1.25-1.25 1.25m-4.25-1.5h4v-8.435L12 3.795 4.5 11.07v8.435h4V16c0-1.93 1.57-3.5 3.5-3.5s3.5 1.57 3.5 3.5z"/>',
    'fullscreen': '<path d="M3 9V3h6v1.5H4.5V9zm12-6h6v6h-1.5V4.5H15zM4.5 15v4.5H9V21H3v-6zM21 15v6h-6v-1.5h4.5V15z"/>',
    'link-alt-02': '<path fill-rule="evenodd" d="M10.04 13.965a5.22 5.22 0 0 0 3.71 1.535h.005c1.34 0 2.685-.51 3.71-1.535l3-3a5.26 5.26 0 0 0 0-7.425 5.26 5.26 0 0 0-7.425 0l-2.5 2.5L11.6 7.1l2.5-2.5a3.76 3.76 0 0 1 5.305 0 3.76 3.76 0 0 1 0 5.305l-3 3a3.76 3.76 0 0 1-5.305 0 3.8 3.8 0 0 1-.59-.77l-1.295.755c.225.395.505.755.825 1.075m-6.5 6.5A5.22 5.22 0 0 0 7.25 22h.005c1.34 0 2.685-.51 3.71-1.535l2.5-2.5-1.06-1.06-2.5 2.5a3.76 3.76 0 0 1-5.305 0 3.76 3.76 0 0 1 0-5.305l3-3a3.76 3.76 0 0 1 5.305 0c.23.23.425.49.59.77l1.295-.755a5 5 0 0 0-.825-1.075 5.26 5.26 0 0 0-7.425 0l-3 3a5.26 5.26 0 0 0 0 7.425" clip-rule="evenodd"/>',
    'play': '<path d="M7.25 20.18c-.215 0-.43-.055-.625-.17A1.24 1.24 0 0 1 6 18.925V5.07a1.251 1.251 0 0 1 1.875-1.085l12 6.93c.39.225.625.63.625 1.085s-.235.855-.625 1.085l-12 6.93c-.195.115-.41.17-.625.17zM7.5 5.505v12.99L18.75 12z"/>',
    'plus': '<path d="M20 11.25h-7.25V4h-1.5v7.25H4v1.5h7.25V20h1.5v-7.25H20z"/>',
    'refresh-cw': '<path d="M14 10h5.75c.69 0 1.25-.56 1.25-1.25V3h-1.5v4.03A8.98 8.98 0 0 0 12 3c-4.96 0-9 4.04-9 9s4.04 9 9 9a9.01 9.01 0 0 0 8.695-6.67l-1.45-.39A7.51 7.51 0 0 1 12 19.5c-4.135 0-7.5-3.365-7.5-7.5S7.865 4.5 12 4.5c2.805 0 5.34 1.55 6.635 4H14z"/>',
    'search': '<path d="m21.78 20.72-5.62-5.62A7.96 7.96 0 0 0 18 10c0-4.41-3.59-8-8-8s-8 3.59-8 8 3.59 8 8 8a7.96 7.96 0 0 0 5.1-1.84l5.62 5.62zM10 16.5A6.506 6.506 0 0 1 3.5 10c0-3.585 2.915-6.5 6.5-6.5s6.5 2.915 6.5 6.5-2.915 6.5-6.5 6.5"/>',
    'share': '<path fill-rule="evenodd" d="M18.5 8a3.5 3.5 0 1 0-3.35-4.5L8.9 7.05a3.5 3.5 0 1 0 0 5.9l6.25 3.55a3.5 3.5 0 1 0 .74-1.31L9.64 11.65a3.5 3.5 0 0 0 0-3.3l6.25-3.54A3.5 3.5 0 0 0 18.5 8m0-5.5a2 2 0 1 1 0 4 2 2 0 0 1 0-4M5.5 8a2 2 0 1 1 0 4 2 2 0 0 1 0-4m13 8a2 2 0 1 1 0 4 2 2 0 0 1 0-4" clip-rule="evenodd"/>',
    'share-ios': '<path fill-rule="evenodd" d="M11.25 14.75V4L9.405 5.845l-1.06-1.06 2.77-2.77a1.26 1.26 0 0 1 1.77 0l2.77 2.77-1.06 1.06L12.75 4v10.75zM14 7h3.25A2.755 2.755 0 0 1 20 9.75v9.5A2.755 2.755 0 0 1 17.25 22H6.75A2.755 2.755 0 0 1 4 19.25v-9.5A2.755 2.755 0 0 1 6.75 7H10v1.5H6.75c-.69 0-1.25.56-1.25 1.25v9.5c0 .69.56 1.25 1.25 1.25h10.5c.69 0 1.25-.56 1.25-1.25v-9.5c0-.69-.56-1.25-1.25-1.25H14z" clip-rule="evenodd"/>',
    'skip-back': '<path fill-rule="evenodd" d="M16.87 19.18c.235.24.555.365.88.365v.005A1.245 1.245 0 0 0 19 18.3V5.715c0-.51-.3-.96-.77-1.155a1.24 1.24 0 0 0-1.36.27l-7.175 7.175zm.63-1.49L11.81 12l5.69-5.69zM5 4.5h1.5v15H5z" clip-rule="evenodd"/>',
    'skip-forward': '<path fill-rule="evenodd" d="M5.77 19.45c.155.065.32.095.48.095v-.01c.32 0 .64-.125.88-.365l7.175-7.175L7.13 4.82a1.24 1.24 0 0 0-1.36-.27C5.305 4.745 5 5.2 5 5.705v12.59c0 .51.3.96.77 1.155M12.19 12 6.5 17.69V6.31zm5.31-7.5H19v15h-1.5z" clip-rule="evenodd"/>',
    'sparks': '<path fill-rule="evenodd" d="M10.69 1.98c0 4 3.25 7.25 7.25 7.25v1.5c-3.995 0-7.25 3.25-7.25 7.25h-1.5c0-4-3.25-7.25-7.25-7.25v-1.5c4 0 7.25-3.25 7.25-7.25zm-5.22 8a8.78 8.78 0 0 1 4.47 4.47 8.78 8.78 0 0 1 4.47-4.47 8.78 8.78 0 0 1-4.47-4.47 8.78 8.78 0 0 1-4.47 4.47" clip-rule="evenodd"/>',
    'trash': '<path d="M21 5h-5v-.25A2.755 2.755 0 0 0 13.25 2h-2.5A2.755 2.755 0 0 0 8 4.75V5H3v1.5h1v12.75A2.755 2.755 0 0 0 6.75 22h10.5A2.755 2.755 0 0 0 20 19.25V6.5h1zM9.5 4.75c0-.69.56-1.25 1.25-1.25h2.5c.69 0 1.25.56 1.25 1.25V5h-5zm9 14.5c0 .69-.56 1.25-1.25 1.25H6.75c-.69 0-1.25-.56-1.25-1.25V6.5h13z"/>',
    'undo': '<path d="M15 7H5.06l2.97-2.97-1.06-1.06-3.895 3.895a1.26 1.26 0 0 0 0 1.77L6.97 12.53l1.06-1.06L5.06 8.5H15c2.48 0 4.5 2.02 4.5 4.5s-2.02 4.5-4.5 4.5H7V19h8c3.31 0 6-2.69 6-6s-2.69-6-6-6"/>',
    'user': '<path fill-rule="evenodd" d="M12 13a5.5 5.5 0 1 0 0-11 5.5 5.5 0 0 0 0 11m0-9.5a4 4 0 1 1 0 8 4 4 0 0 1 0-8M2.5 22v-2.25A5.756 5.756 0 0 1 8.25 14h7.5a5.756 5.756 0 0 1 5.75 5.75V22H20v-2.25a4.255 4.255 0 0 0-4.25-4.25h-7.5A4.255 4.255 0 0 0 4 19.75V22z" clip-rule="evenodd"/>',
    'user-plus': '<path fill-rule="evenodd" d="M10.5 13c3.035 0 5.5-2.465 5.5-5.5S13.535 2 10.5 2A5.503 5.503 0 0 0 5 7.5c0 3.035 2.465 5.5 5.5 5.5m0-9.5c2.205 0 4 1.795 4 4s-1.795 4-4 4-4-1.795-4-4 1.795-4 4-4m10 8H23V13h-2.5v2.5H19V13h-2.5v-1.5H19V9h1.5z" clip-rule="evenodd"/>'
  };

  function loadTokens() {
    const current = JSON.parse(localStorage.getItem(storageKey) || '{}');
    const legacy = JSON.parse(localStorage.getItem(legacyKey) || '{}');
    const tokens = {...legacy, ...current};
    localStorage.setItem(storageKey, JSON.stringify(tokens));
    if (localStorage.getItem(legacyKey)) localStorage.removeItem(legacyKey);
    return tokens;
  }

  function knownUsers() {
    try {
      const users = JSON.parse(localStorage.getItem(usersKey) || '[]');
      return Array.isArray(users) ? users.filter(user => user?.id && user?.name && user?.token) : [];
    } catch (_error) {
      return [];
    }
  }

  function rememberUser(user) {
    const users = knownUsers().filter(candidate => candidate.id !== user.id);
    users.push({id: user.id, name: user.name, token: user.token});
    localStorage.setItem(usersKey, JSON.stringify(users));
    return user;
  }

  function activeUser() {
    const users = knownUsers();
    const token = localStorage.getItem(activeUserKey);
    const active = users.find(user => user.token === token) || users.at(-1) || null;
    if (active && active.token !== token) localStorage.setItem(activeUserKey, active.token);
    return active;
  }

  function setActiveUser(user) {
    rememberUser(user);
    localStorage.setItem(activeUserKey, user.token);
    return user;
  }

  function clearActiveUser() {
    const fallback = knownUsers().at(-1);
    if (fallback) localStorage.setItem(activeUserKey, fallback.token);
    else localStorage.removeItem(activeUserKey);
    return fallback || null;
  }

  function authFetch(path, options = {}, user = activeUser()) {
    if (!user) return Promise.reject(new Error('Choose a player first'));
    const headers = new Headers(options.headers || {});
    headers.set('Authorization', `Bearer ${user.token}`);
    return fetch(path, {...options, headers});
  }

  function saveToken(roomID, token) {
    const tokens = loadTokens();
    tokens[roomID] = token;
    localStorage.setItem(storageKey, JSON.stringify(tokens));
  }

  function removeToken(roomID) {
    const tokens = loadTokens();
    delete tokens[roomID];
    localStorage.setItem(storageKey, JSON.stringify(tokens));
  }

  function escapeHTML(value) {
    const node = document.createElement('span');
    node.textContent = String(value ?? '');
    return node.innerHTML;
  }

  function icon(name, label = '') {
    const body = icons[name];
    if (!body) return '';
    const accessible = label ? ` role="img" aria-label="${escapeHTML(label)}"` : ' aria-hidden="true"';
    return `<svg class="griddy-icon" viewBox="0 0 24 24" fill="currentColor"${accessible}>${body}</svg>`;
  }

  function hydrateIcons(root = document) {
    root.querySelectorAll('[data-icon]').forEach(node => {
      node.innerHTML = icon(node.dataset.icon, node.dataset.iconLabel || '');
    });
  }

  return {
    loadTokens, saveToken, removeToken,
    knownUsers, rememberUser, activeUser, setActiveUser, clearActiveUser, authFetch,
    escapeHTML, icon, hydrateIcons,
  };
})();

PnPInkPlay.hydrateIcons();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(error => console.warn('PWA registration failed', error));
  });
}
