// ── Side panel toggle ─────────────────────────────────────────────────────────
const panel = document.getElementById('panel');
const panelToggle = document.getElementById('panel-toggle');
panelToggle.addEventListener('click', () => {
  panel.classList.toggle('collapsed');
});

// In portrait viewports, interacting with the map or slider closes the panel
// so it doesn't cover the content underneath.
function isPortrait() { return window.innerWidth <= window.innerHeight; }
function closePanelIfPortrait() {
  if (isPortrait()) panel.classList.add('collapsed');
}
map.on('click', closePanelIfPortrait);
map.on('movestart', (e) => { if (e.originalEvent) closePanelIfPortrait(); }); // only user-initiated moves, not programmatic resize
