/* SpawnWP contact concierge — a scripted (non-AI) chat that collects an intent,
   the visitor's message and email (with consent), then posts to the first-party
   /api/v1/contact endpoint. No third parties, no cookies, no CSP changes.
   The destination address is never exposed in the page. */
(() => {
  'use strict';

  const REPO = 'tts-empire/spawnwp';
  const ENDPOINT = '/api/v1/contact';
  const ISSUES_URL = `https://github.com/${REPO}/issues/new`;
  const DISCUSSIONS_URL = `https://github.com/${REPO}/discussions/new?category=q-a`;
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  const INTENTS = [
    { id: 'support', label: 'Question / support' },
    { id: 'bug', label: 'Report a bug' },
    { id: 'business', label: 'Business / private' },
    { id: 'security', label: 'Security' },
  ];

  const el = (tag, props = {}, children = []) => {
    const node = document.createElement(tag);
    Object.assign(node, props);
    for (const child of [].concat(children)) {
      if (child != null) node.append(child.nodeType ? child : document.createTextNode(child));
    }
    return node;
  };

  let panel, log, foot, launcher, started = false;
  const state = { intent: null, message: '' };

  const scrollDown = () => { log.scrollTop = log.scrollHeight; };
  const bot = (text) => { log.append(el('div', { className: 'cx-msg bot', textContent: text })); scrollDown(); };
  const user = (text) => { log.append(el('div', { className: 'cx-msg user', textContent: text })); scrollDown(); };
  const clearFoot = () => { foot.replaceChildren(); };

  const choices = (items) => {
    const wrap = el('div', { className: 'cx-choices' });
    for (const item of items) {
      if (item.href) {
        wrap.append(el('a', { className: 'cx-link', href: item.href, target: '_blank', rel: 'noopener noreferrer', textContent: item.label }));
      } else {
        const btn = el('button', { type: 'button', className: item.primary ? 'primary' : '', textContent: item.label });
        btn.addEventListener('click', item.onClick);
        wrap.append(btn);
      }
    }
    return wrap;
  };

  function askIntent() {
    bot('Hi! 👋 What can I help you with?');
    clearFoot();
    foot.append(choices(INTENTS.map((it) => ({
      label: it.label,
      onClick: () => pickIntent(it),
    }))));
  }

  function pickIntent(intent) {
    state.intent = intent.id;
    user(intent.label);
    clearFoot();
    if (intent.id === 'support') {
      bot('Happy to help. Ask publicly on GitHub Discussions (great for a searchable answer), or write to me privately here.');
      foot.append(choices([
        { label: 'Ask on Discussions ↗', href: DISCUSSIONS_URL },
        { label: 'Write privately', primary: true, onClick: startPrivate },
      ]));
    } else if (intent.id === 'bug') {
      bot('Bugs are best tracked as a GitHub Issue so they don’t get lost. Open one, or describe it here and I’ll take it from there.');
      foot.append(choices([
        { label: 'Open an Issue ↗', href: ISSUES_URL },
        { label: 'Describe it here', primary: true, onClick: startPrivate },
      ]));
    } else if (intent.id === 'security') {
      bot('Thanks for the care. Please don’t post security details publicly — write them privately here and I’ll follow up.');
      askForm();
    } else {
      bot('Sure — tell me a bit about it and leave an email so I can get back to you.');
      askForm();
    }
  }

  function startPrivate() {
    bot('Go ahead — write your message and the email I should reply to.');
    askForm();
  }

  // Single step: message + email + consent, one Send button.
  function askForm() {
    clearFoot();
    const field = el('div', { className: 'cx-field' });
    const textarea = el('textarea', { placeholder: 'Your message…', maxLength: 4000 });
    const email = el('input', { type: 'email', placeholder: 'you@example.com', autocomplete: 'email', maxLength: 254 });

    const consentId = 'cx-consent-' + Math.random().toString(36).slice(2);
    const consent = el('input', { type: 'checkbox', id: consentId });
    const consentLabel = el('label', { className: 'cx-consent', htmlFor: consentId }, [
      consent,
      el('span', { textContent: 'I agree to my email being used only to reply to this request.' }),
    ]);

    // Honeypot: hidden from humans, tempting for bots. Must stay empty.
    const honeypot = el('input', { type: 'text', className: 'cx-honeypot', tabIndex: -1, autocomplete: 'off', name: 'website' });
    honeypot.setAttribute('aria-hidden', 'true');

    const send = el('button', { type: 'button', className: 'cx-send', textContent: 'Send', disabled: true });
    const refresh = () => {
      send.disabled = !(textarea.value.trim() && EMAIL_RE.test(email.value.trim()) && consent.checked);
    };
    textarea.addEventListener('input', refresh);
    email.addEventListener('input', refresh);
    consent.addEventListener('change', refresh);

    send.addEventListener('click', () => {
      state.message = textarea.value.trim();
      submit({ email: email.value.trim(), consent: consent.checked, honeypot: honeypot.value }, send);
    });

    field.append(textarea, email, consentLabel, honeypot);
    const actions = el('div', { className: 'cx-actions' });
    actions.append(send, backButton(askIntent));
    foot.append(field, actions);
    textarea.focus();
  }

  async function submit(data, sendBtn) {
    if (!state.message || !EMAIL_RE.test(data.email) || !data.consent) return;
    user(state.message);
    user(data.email);
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending…';
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          intent: state.intent,
          email: data.email,
          message: state.message,
          consent: true,
          website: data.honeypot || '',
        }),
      });
      if (!res.ok) throw new Error('bad status ' + res.status);
      clearFoot();
      bot('Thanks — got it. I’ll reply to you soon. 👋');
    } catch (err) {
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send';
      bot('Hmm, that didn’t go through. Please try again in a moment.');
    }
  }

  function backButton(onClick) {
    const back = el('button', { type: 'button', className: 'cx-back', textContent: '← Back' });
    back.addEventListener('click', onClick);
    return back;
  }

  function open() {
    panel.hidden = false;
    launcher.hidden = true;
    if (!started) { started = true; askIntent(); }
    const first = foot.querySelector('button, a, textarea, input');
    if (first) first.focus();
  }

  function close() {
    panel.hidden = true;
    launcher.hidden = false;
    launcher.focus();
  }

  function build() {
    launcher = el('button', { type: 'button', className: 'cx-launcher', 'aria-label': 'Open the contact concierge' });
    launcher.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5a8.5 8.5 0 0 1-12.4 7.5L3 20l1.1-3.3A8.5 8.5 0 1 1 21 11.5Z"/></svg>';
    launcher.append(el('span', { textContent: 'Need a hand?' }));
    launcher.addEventListener('click', open);

    const head = el('div', { className: 'cx-head' }, [
      el('span', { className: 'cx-dot' }),
      el('b', { textContent: 'SpawnWP concierge' }),
    ]);
    const close_ = el('button', { type: 'button', className: 'cx-close', 'aria-label': 'Close', textContent: '×' });
    close_.addEventListener('click', close);
    head.append(close_);

    log = el('div', { className: 'cx-log', role: 'log', 'aria-live': 'polite' });
    foot = el('div', { className: 'cx-foot' });

    panel = el('div', { className: 'cx-panel', role: 'dialog', 'aria-label': 'Contact concierge', hidden: true });
    panel.append(head, log, foot);

    document.body.append(launcher, panel);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) close(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
