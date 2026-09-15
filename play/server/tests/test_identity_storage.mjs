import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../boardzilla_lite/web/common.js', import.meta.url), 'utf8');

function storage(values = new Map()) {
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  };
}

function tab(localStorage, sessionStorage) {
  const context = {
    Headers,
    fetch: () => Promise.reject(new Error('Unexpected fetch')),
    localStorage,
    sessionStorage,
    navigator: {},
    window: {addEventListener() {}},
    document: {
      createElement: () => ({textContent: '', innerHTML: ''}),
      querySelectorAll: () => [],
    },
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nglobalThis.identity = PnPInkPlay;`, context);
  return context.identity;
}

const sharedUsers = storage();
const xoelTab = tab(sharedUsers, storage());
xoelTab.setActiveUser({id: 'xoel', name: 'Xoel', token: 'token-x'});
const anaTab = tab(sharedUsers, storage());
anaTab.setActiveUser({id: 'ana', name: 'Ana', token: 'token-a'});

assert.equal(xoelTab.activeUser().name, 'Ana');
assert.equal(anaTab.activeUser().name, 'Ana');
assert.equal(xoelTab.knownUsers().length, 2);
assert.equal(tab(sharedUsers, storage()).activeUser().name, 'Ana');
