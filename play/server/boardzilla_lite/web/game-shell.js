(() => {
  let details;
  try {
    details = JSON.parse(document.body.dataset.bootstrapJson || '{}');
  } catch (_error) {
    return;
  }

  function setupHero(setup) {
    if (setup.querySelector('.pnpplay-setup-hero')) return;
    const inviteURL = new URL(details.inviteURL || '/', location.origin).href;
    const hero = document.createElement('section');
    hero.className = 'pnpplay-setup-hero';
    hero.innerHTML = `
      <div class="pnpplay-setup-cover" aria-hidden="true"><span></span></div>
      <div class="pnpplay-setup-copy">
        <div class="pnpplay-setup-kicker">Game setup</div>
        <h1></h1>
        <p>Invite other players to join this table.</p>
        <div class="pnpplay-invite-link"><input readonly aria-label="Invitation link"><button type="button">Copy link</button></div>
      </div>`;
    hero.querySelector('h1').textContent = details.gameName || 'Game';
    hero.querySelector('.pnpplay-setup-cover span').textContent = String(details.gameName || 'P').slice(0, 1);
    if (details.gameCover) hero.querySelector('.pnpplay-setup-cover').style.backgroundImage = `url('${details.gameCover}')`;
    const input = hero.querySelector('input');
    const button = hero.querySelector('button');
    input.value = inviteURL;
    input.addEventListener('click', () => input.select());
    button.addEventListener('click', async () => {
      input.select();
      try {
        await navigator.clipboard.writeText(inviteURL);
      } catch (_error) {
        document.execCommand('copy');
      }
      button.textContent = 'Copied';
      setTimeout(() => { button.textContent = 'Copy link'; }, 1400);
    });
    const panel = [...setup.children].find(node => node.id !== 'background');
    panel?.prepend(hero);
  }

  function syncSetup() {
    const setup = document.querySelector('#setup');
    document.documentElement.classList.toggle('pnpplay-setup-active', Boolean(setup));
    document.body.classList.toggle('pnpplay-setup-active', Boolean(setup));
    if (setup) setupHero(setup);
  }

  const observer = new MutationObserver(syncSetup);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  syncSetup();
})();
