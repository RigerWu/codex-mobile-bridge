(() => {
  if (window !== window.top) return;
  const persist = () => window.prompt('codexbridge-session:__BRIDGE_CLIPBOARD_TOKEN__', '');
  document.addEventListener('bridge-authenticated', persist);
  document.addEventListener('bridge-logged-out', () => window.prompt('codexbridge-logout:__BRIDGE_CLIPBOARD_TOKEN__', ''));
  document.addEventListener('bridge-auth-lost', () => window.prompt('codexbridge-auth-lost:__BRIDGE_CLIPBOARD_TOKEN__', ''));
  const app = document.getElementById('app');
  if (app && !app.hidden) persist();
})();
