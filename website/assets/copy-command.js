// Shared successful-copy signal for the homepage and installation docs.
(() => {
  'use strict';
  if (window.spawnwpCommandCopyLoaded) return;
  window.spawnwpCommandCopyLoaded = true;

  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const input = document.createElement('textarea');
    input.value = text;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    try {
      input.select();
      if (!document.execCommand('copy')) throw new Error('Copy unavailable');
    } finally {
      input.remove();
    }
  }

  // Capture only marked commands before Material's delegated clipboard listener.
  // Other code blocks retain Material's behavior, including after instant loads.
  document.addEventListener('click', async (event) => {
    const button = event.target.closest && event.target.closest(
      '[data-copy-command], .spawnwp-install-command [data-md-type="copy"]'
    );
    if (!button) return;
    const block = button.closest('.terminal, .spawnwp-install-command');
    const code = block?.querySelector('code');
    if (!code) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (button.dataset.copyPending) return;
    const path = window.location.pathname;
    const original = { html: button.innerHTML, label: button.getAttribute('aria-label'), title: button.getAttribute('title') };
    button.dataset.copyPending = 'true';
    try {
      await copy(code.textContent.trim());
      document.dispatchEvent(new CustomEvent('spawnwp:command-copied', { detail: { path } }));
      button.classList.add('is-copied');
      button.setAttribute('aria-label', 'Installation command copied');
      button.setAttribute('title', 'Copied');
      button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"></path></svg>';
    } catch (_error) {
      button.setAttribute('aria-label', 'Unable to copy; select the command manually');
      button.setAttribute('title', 'Unable to copy');
    } finally {
      window.setTimeout(() => {
        button.classList.remove('is-copied');
        button.innerHTML = original.html;
        for (const [attribute, value] of [['aria-label', original.label], ['title', original.title]]) {
          if (value === null) button.removeAttribute(attribute);
          else button.setAttribute(attribute, value);
        }
        delete button.dataset.copyPending;
      }, 1600);
    }
  }, true);
})();
