'use strict';

// Read-only source rendering shared by file preview and source-mode consumers.
const BridgeSourceView = (() => {
  const extensions = {
    py:'python', pyw:'python', js:'javascript', mjs:'javascript', cjs:'javascript', jsx:'jsx',
    ts:'typescript', mts:'typescript', cts:'typescript', tsx:'tsx', json:'json', jsonc:'json',
    yaml:'yaml', yml:'yaml', toml:'toml', html:'markup', htm:'markup', xml:'markup', svg:'markup',
    css:'css', sh:'bash', bash:'bash', zsh:'bash', ps1:'powershell', psm1:'powershell',
    cmd:'batch', bat:'batch'
  };
  const languageFor = name => {
    const file = name.split('/').pop().toLowerCase();
    const extension = file.includes('.') ? file.split('.').pop() : '';
    return extensions[extension] || null;
  };
  const appendTokens = (parent, tokens) => {
    for (const token of Array.isArray(tokens) ? tokens : [tokens]) {
      if (typeof token === 'string') { parent.append(document.createTextNode(token)); continue; }
      const span = document.createElement('span');
      span.className = ['token', token.type, ...[].concat(token.alias || [])].join(' ');
      appendTokens(span, token.content);
      parent.append(span);
    }
  };
  const create = ({name, text}) => {
    const source = document.createElement('div'); source.className = 'wb-source';
    let lines = 1;
    for (let index = 0; index < text.length; index++) {
      const character = text.charCodeAt(index);
      if (character === 10 || character === 13 && text.charCodeAt(index + 1) !== 10) lines++;
    }
    const large = text.length > 65536 || lines > 4000;
    const code = document.createElement('code');
    const language = languageFor(name);
    const grammar = !large && language && window.Prism?.languages[language];
    if (grammar) {
      try { appendTokens(code, window.Prism.tokenize(text, grammar)); }
      catch (_) { code.textContent = text; }
    } else code.textContent = text;
    if (language) code.className = 'language-' + language;
    if (lines <= 10000) {
      const gutter = document.createElement('pre'); gutter.className = 'wb-source-gutter';
      gutter.setAttribute('aria-hidden', 'true');
      gutter.textContent = Array.from({length:lines}, (_, index) => String(index + 1)).join('\n');
      source.append(gutter);
    }
    const pre = document.createElement('pre'); pre.className = 'wb-source-code'; pre.append(code);
    source.append(pre);
    if (large) source.dataset.plain = 'true';
    return source;
  };
  return {create, languageFor};
})();
