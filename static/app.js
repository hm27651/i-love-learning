(() => {
  const root = document.documentElement;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  document.querySelectorAll('form').forEach(form => {
    if ((form.method || 'get').toLowerCase() !== 'post' || form.querySelector('input[name="_csrf_token"]')) return;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = '_csrf_token';
    input.value = csrfToken;
    form.appendChild(input);
  });
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request?.url || input, window.location.href);
    const method = String(options.method || request?.method || 'GET').toUpperCase();
    if (url.origin === window.location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(options.headers || request?.headers || {});
      headers.set('X-CSRF-Token', csrfToken);
      options = {...options, headers};
    }
    return nativeFetch(input, options);
  };
  const themeNames = {system: '跟随系统', light: '浅色模式', dark: '深色模式'};

  function syncThemeLabels() {
    document.querySelectorAll('[data-theme-label]').forEach(el => el.textContent = themeNames[root.dataset.theme] || themeNames.system);
  }
  document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
    const current = root.dataset.theme || 'system';
    const next = current === 'system' ? 'light' : current === 'light' ? 'dark' : 'system';
    root.dataset.theme = next;
    localStorage.setItem('study-theme', next);
    syncThemeLabels();
  }));
  syncThemeLabels();

  if (window.matchMedia('(max-width: 760px)').matches) {
    document.querySelectorAll('[data-mobile-filter]').forEach(details => details.removeAttribute('open'));
  }

  function syncSidebarToggleState() {
    const collapsed = root.classList.contains('sidebar-collapsed');
    document.querySelectorAll('[data-sidebar-toggle]').forEach(button => {
      button.setAttribute('aria-expanded', String(!collapsed));
      if (button.classList.contains('sidebar-toggle')) {
        button.setAttribute('aria-label', collapsed ? '展开侧栏' : '收起侧栏');
      }
    });
  }
  document.querySelectorAll('[data-sidebar-toggle]').forEach(button => button.addEventListener('click', () => {
    root.classList.toggle('sidebar-collapsed');
    localStorage.setItem('study-sidebar', root.classList.contains('sidebar-collapsed') ? 'collapsed' : 'expanded');
    syncSidebarToggleState();
  }));
  syncSidebarToggleState();

  const sheet = document.querySelector('.more-sheet');
  const backdrop = document.querySelector('.sheet-backdrop');
  function setSheet(open) {
    if (!sheet || !backdrop) return;
    sheet.classList.toggle('open', open);
    backdrop.classList.toggle('open', open);
    sheet.setAttribute('aria-hidden', String(!open));
    document.body.style.overflow = open ? 'hidden' : '';
  }
  document.querySelectorAll('[data-more-toggle]').forEach(el => el.addEventListener('click', () => setSheet(true)));
  document.querySelectorAll('[data-more-close]').forEach(el => el.addEventListener('click', () => setSheet(false)));

  document.querySelectorAll('[data-confirm]').forEach(el => el.addEventListener('click', event => {
    if (!confirm(el.dataset.confirm)) event.preventDefault();
  }));

  document.querySelectorAll('[data-terminate-open]').forEach(button => button.addEventListener('click', () => {
    const dialog = document.querySelector(button.dataset.terminateOpen);
    if (dialog) dialog.showModal();
  }));
  document.querySelectorAll('[data-terminate-dialog]').forEach(dialog => {
    dialog.querySelectorAll('[data-terminate-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  });

  document.querySelectorAll('[data-check-all]').forEach(el => el.addEventListener('change', () => {
    document.querySelectorAll(el.dataset.checkAll).forEach(box => { box.checked = el.checked; box.dispatchEvent(new Event('change')); });
  }));
  const selectedCount = document.querySelector('[data-selected-count]');
  function syncSelectedCount() {
    if (!selectedCount) return;
    selectedCount.textContent = document.querySelectorAll('input[name="question_id"]:checked').length;
  }
  document.querySelectorAll('input[name="question_id"]').forEach(box => box.addEventListener('change', syncSelectedCount));
  syncSelectedCount();

  const dialog = document.querySelector('[data-image-dialog]');
  if (dialog) {
    const largeImage = dialog.querySelector('img');
    document.querySelectorAll('.question-image').forEach(image => {
      image.tabIndex = 0;
      image.setAttribute('role', 'button');
      image.setAttribute('aria-label', '点击查看大图');
      const open = () => { largeImage.src = image.src; largeImage.alt = image.alt || '题目大图'; dialog.showModal(); };
      image.addEventListener('click', open);
      image.addEventListener('keydown', event => { if (event.key === 'Enter') open(); });
    });
    dialog.querySelector('[data-image-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  }

  const practiceForm = document.querySelector('[data-practice-form]');
  if (practiceForm) {
    document.addEventListener('keydown', event => {
      if (event.ctrlKey || event.altKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
      const number = Number(event.key);
      if (number >= 1 && number <= 9) {
        const option = practiceForm.querySelectorAll('input[name="answer"]')[number - 1];
        if (option) { option.click(); option.closest('.option')?.focus(); }
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (practiceForm.reportValidity()) practiceForm.requestSubmit();
      }
    });
  }

  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.altKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    const link = event.key === 'ArrowLeft' ? document.querySelector('[data-review-prev]') : event.key === 'ArrowRight' ? document.querySelector('[data-review-next]') : null;
    if (link) link.click();
    if (event.key.toLowerCase() === 'v') document.querySelector('[data-review-verify]')?.click();
    if (event.key.toLowerCase() === 'a') document.querySelector('[data-review-archive]')?.click();
  });
})();
