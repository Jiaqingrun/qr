/** QR Web 导航：体验档位、组 Tab、今日首页、命令面板、首次向导 */
(function (g) {
  const $ = g.$ || ((s) => document.querySelector(s));
  const $$ = g.$$ || ((s) => document.querySelectorAll(s));
  const esc = g.esc || ((s) => String(s || ''));

  const NAV_GROUPS = {
    record: {
      label: '足迹',
      nav: 'record',
      tabs: [
        { id: 'timeline', label: '时间线' },
        { id: 'summary', label: '总结' },
        { id: 'usage', label: '应用', minTier: 'daily' },
      ],
    },
    project: {
      label: '项目',
      nav: 'project',
      tabs: [
        { id: 'project', label: '概览' },
        { id: 'relations', label: '关系', minTier: 'full' },
      ],
    },
    settings: {
      label: '设置',
      nav: 'settings',
      tabs: [
        { id: 'ops', label: '系统' },
        { id: 'standards', label: '规范' },
        { id: 'acceptance', label: '验收', minTier: 'daily' },
      ],
    },
  };

  const VIEW_TO_GROUP = {};
  Object.keys(NAV_GROUPS).forEach((gk) => {
    NAV_GROUPS[gk].tabs.forEach((t) => {
      VIEW_TO_GROUP[t.id] = gk;
    });
  });

  const PRIMARY_NAV = {
    today: { view: 'today', label: '今天' },
    ask: { view: 'ask', label: '问答' },
    record: { view: 'timeline', label: '足迹' },
    project: { view: 'project', label: '项目' },
    prompts: { view: 'prompts', label: '引导语' },
    settings: { view: 'ops', label: '设置' },
  };

  const MORE_VIEWS = ['stdlog', 'insight', 'console', 'query'];

  let uiTier = 'full';
  let navMap = null;
  let landingView = 'ask';

  function tierRank(t) {
    return { starter: 0, daily: 1, full: 2 }[t] ?? 2;
  }

  function tabVisible(tab) {
    if (!tab.minTier) return true;
    return tierRank(uiTier) >= tierRank(tab.minTier);
  }

  function resolveNavItem(view) {
    const gk = VIEW_TO_GROUP[view];
    if (gk) return NAV_GROUPS[gk].nav;
    if (view === 'today') return 'today';
    if (MORE_VIEWS.includes(view)) return 'more';
    return view;
  }

  function renderPageTabs(view) {
    const bar = $('#pageTabs');
    if (!bar) return;
    const gk = VIEW_TO_GROUP[view];
    if (!gk || uiTier === 'full') {
      bar.hidden = true;
      bar.innerHTML = '';
      return;
    }
    const group = NAV_GROUPS[gk];
    const tabs = group.tabs.filter(tabVisible);
    bar.hidden = false;
    bar.innerHTML = tabs
      .map(
        (t) =>
          `<button type="button" class="page-tab${t.id === view ? ' active' : ''}" data-view="${esc(t.id)}">${esc(t.label)}</button>`,
      )
      .join('');
    bar.querySelectorAll('.page-tab').forEach((btn) => {
      btn.onclick = () => {
        if (typeof g.switchView === 'function') g.switchView(btn.dataset.view);
      };
    });
  }

  function syncNavActive(view) {
    const navKey = resolveNavItem(view);
    $$('.nav-item').forEach((x) => {
      const v = x.dataset.v;
      const navView = x.dataset.navView || v;
      x.classList.toggle('active', navView === navKey || v === view);
    });
    renderPageTabs(view);
  }

  function applyUiTier(tier, map) {
    uiTier = tier || 'full';
    navMap = map || null;
    document.body.dataset.uiTier = uiTier;
    const nav = $('#nav');
    const navFull = $('#navFull');
    const navSimple = $('#navSimple');
    if (navFull) navFull.hidden = uiTier !== 'full';
    if (navSimple) navSimple.hidden = uiTier === 'full';
    const tierBanner = $('#opsUiTierBanner');
    if (tierBanner) tierBanner.hidden = uiTier !== 'full';
    const tierSwitch = $('#sidebarTierSwitch');
    if (tierSwitch) tierSwitch.hidden = uiTier !== 'full';
    const tierLabel = $('#sidebarTierDaily')?.querySelector('.sidebar-tier-label');
    if (tierLabel) {
      tierLabel.textContent =
        uiTier === 'starter' ? '当前：入门版' : uiTier === 'daily' ? '当前：日常版' : '当前：完整版';
    }
    if (nav) nav.dataset.mode = uiTier;
  }

  async function setUiTier(tier) {
    if (!tier) return;
    const landing = tier === 'daily' || tier === 'starter' ? 'today' : 'ask';
    try {
      await g.api('/api/ui-tier', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier, landing_view: landing }),
      });
      logUiEvent('tier_change', { detail: tier });
      location.reload();
    } catch (e) {
      alert('切换失败：' + (e.message || e));
    }
  }

  async function switchToDailyTier() {
    if (!confirm('切换到「日常」界面？\n\n侧栏将变为：今天 / 问答 / 足迹 / 项目 / 引导语 / 设置')) return;
    await setUiTier('daily');
  }

  function goAcceptanceView() {
    if (typeof g.switchView === 'function') g.switchView('acceptance');
  }

  function initTierInteraction() {
    document.addEventListener(
      'click',
      (e) => {
        const tierBtn = e.target.closest('.ui-tier-btn');
        if (tierBtn?.dataset.tier) {
          e.preventDefault();
          e.stopPropagation();
          void setUiTier(tierBtn.dataset.tier);
          return;
        }
        if (e.target.closest('#sidebarGoAcceptance')) {
          e.preventDefault();
          e.stopPropagation();
          goAcceptanceView();
          return;
        }
        if (e.target.closest('#sidebarGoFullTier')) {
          e.preventDefault();
          e.stopPropagation();
          if (!confirm('切换到「完整」版？\n\n侧栏将恢复 13 项（核心 / 记录 / 治理）。')) return;
          void setUiTier('full');
          return;
        }
        if (e.target.closest('#sidebarTierSwitch')) {
          e.preventDefault();
          e.stopPropagation();
          void switchToDailyTier();
        }
      },
      true,
    );
  }

  async function loadUiTier() {
    try {
      const r = await g.api('/api/ui-tier');
      applyUiTier(r.ui_tier, r.nav_map);
      landingView = r.ui_landing_view || (uiTier === 'full' ? 'ask' : 'today');
      return r;
    } catch (_e) {
      applyUiTier('full');
      landingView = 'ask';
      return null;
    }
  }

  function renderResumeBlock(d, prefix) {
    prefix = prefix || 'today';
    if (!d) return;
    const proj = d.active_project || '未识别项目';
    const elMeta = $(`#${prefix}ResumeMeta`);
    if (elMeta) elMeta.textContent = `${proj} · ${d.generated_at || ''}`;
    const acts = d.actions || [];
    const elActs = $(`#${prefix}ResumeActions`);
    if (elActs) {
      elActs.innerHTML = acts.length
        ? acts.map((a) => `<span class="badge badge-focus">${esc(a)}</span>`).join('')
        : '<span class="resume-empty">暂无建议</span>';
    }
    const cur = d.cursor_topics || [];
    const elCur = $(`#${prefix}ResumeCursor`);
    if (elCur) {
      elCur.innerHTML = cur.length
        ? cur.map((it) => `<div class="resume-item"><span class="time">${esc(it.time || '')}</span>${esc(it.title || '')}</div>`).join('')
        : '<div class="resume-empty">近 7 天无 Cursor 记录</div>';
    }
    const git = d.recent_git || [];
    const elGit = $(`#${prefix}ResumeGit`);
    if (elGit) {
      elGit.innerHTML = git.length
        ? git.map((it) => `<div class="resume-item"><span class="time">${esc(it.time || '')}</span>${esc(it.title || '')}</div>`).join('')
        : '<div class="resume-empty">近 14 天无 Git 记录</div>';
    }
    const tasks = (d.open_tasks?.active) || [];
    const byProj = d.open_tasks?.by_project || [];
    let taskHtml = '';
    if (tasks.length) {
      taskHtml += tasks.slice(0, 5).map((t) => `<div class="resume-item">${esc(t)}</div>`).join('');
    }
    if (byProj.length) {
      taskHtml += byProj
        .map((bp) => {
          const lines = (bp.tasks || []).map((t) => `<div class="resume-item">${esc(t)}</div>`).join('');
          return `<div class="resume-item" style="border:none;padding-top:8px"><span class="time">${esc(bp.project || '')}</span></div>${lines}`;
        })
        .join('');
    }
    const elTasks = $(`#${prefix}ResumeTasks`);
    if (elTasks) elTasks.innerHTML = taskHtml || '<div class="resume-empty">README 无未完成项</div>';
  }

  function alertActionBtn(a) {
    const act = a.action || '';
    if (act === 'settings_import' || a.type === 'standards') {
      return `<button type="button" class="btn btn-sm alert-go" data-go="ops">去处理</button>`;
    }
    if (act === 'settings_quality' || a.type === 'rag') {
      return `<button type="button" class="btn btn-sm alert-go" data-go="insight">查看</button>`;
    }
    return `<button type="button" class="btn btn-ghost btn-sm alert-go" data-go="ops">设置</button>`;
  }

  function renderAlertQueue(alerts) {
    const el = $('#todayAlerts');
    if (!el) return;
    const list = (alerts || [])
      .filter((a) => a.priority === 'p0' || a.priority === 'p1' || a.level === 'warn')
      .slice(0, 3);
    if (!list.length) {
      el.innerHTML = '<div class="hint">暂无待处理事项</div>';
      return;
    }
    el.innerHTML = list
      .map(
        (a) =>
          `<div class="today-alert card"><span class="badge ${a.level === 'warn' ? 'warn' : ''}">${esc(a.type || '提醒')}</span><span>${esc(a.message)}</span>${alertActionBtn(a)}</div>`,
      )
      .join('');
    el.querySelectorAll('.alert-go').forEach((btn) => {
      btn.onclick = () => g.switchView(btn.dataset.go === 'insight' ? 'insight' : 'ops');
    });
  }

  async function loadTodayView() {
    const digestEl = $('#todayDigest');
    try {
      const [today, resume] = await Promise.all([g.api('/api/today'), g.api('/api/resume')]);
      renderResumeBlock(resume, 'today');
      renderAlertQueue(today.alerts || []);
      if (digestEl) {
        const txt = (today.digest_preview || '').trim();
        digestEl.textContent = txt || '暂无今日摘要，后台任务运行后会自动生成。';
      }
    } catch (e) {
      if (digestEl) digestEl.textContent = '加载失败：' + (e.message || '');
    }
  }

  function renderHealthSimple(status) {
    const el = $('#sidebarHealthText');
    if (!el || !status) return;
    const issues = (status.health_issues || []).length;
    if (status.health_ok && !issues) {
      el.innerHTML = '<span class="health-dot ok"></span> 系统正常';
    } else {
      el.innerHTML = `<span class="health-dot warn"></span> ${issues || 1} 项待处理`;
    }
  }

  async function loadTopFocusBar() {
    const sel = $('#topFocusSelect');
    if (!sel) return;
    try {
      const r = await g.api('/api/focus-project');
      const projects = r.projects || [];
      sel.innerHTML =
        '<option value="">（自动检测）</option>' +
        projects.map((p) => `<option value="${esc(p.id || p)}">${esc(p.label || p.id || p)}</option>`).join('');
      if (r.focus_project) sel.value = r.focus_project;
    } catch (_e) {}
  }

  async function saveTopFocus(project) {
    await g.api('/api/focus-project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: project || '' }),
    });
    const askProj = $('#askProject');
    if (askProj && project) askProj.value = project;
  }

  function bindTopFocusBar() {
    const sel = $('#topFocusSelect');
    sel?.addEventListener('change', () => saveTopFocus(sel.value));
    $('#topFocusAskBtn')?.addEventListener('click', () => {
      const p = sel?.value;
      if (p && $('#askProject')) $('#askProject').value = p;
      g.switchView('ask');
    });
    $('#topFocusTimelineBtn')?.addEventListener('click', () => g.switchView('timeline'));
  }

  function bindSimpleNav() {
    $$('#navSimple .nav-item').forEach((n) => {
      n.onclick = () => {
        const v = n.dataset.v;
        const view = PRIMARY_NAV[v]?.view || v;
        g.switchView(view);
      };
    });
    $$('#navMore .nav-item').forEach((n) => {
      n.onclick = () => g.switchView(n.dataset.v);
    });
  }

  function bindTodayShortcuts() {
    $('#todayGoAsk')?.addEventListener('click', () => g.switchView('ask'));
    $('#todayGoLog')?.addEventListener('click', () => {
      g.switchView('timeline');
      $('#noteInput')?.focus();
    });
    $('#todayGoTimeline')?.addEventListener('click', () => g.switchView('timeline'));
    $('#todayGoDoctor')?.addEventListener('click', () => {
      if (typeof g.runShipDoctor === 'function') {
        g.switchView('acceptance');
        g.runShipDoctor();
      } else g.switchView('ops');
    });
    $('#todayRefreshBtn')?.addEventListener('click', () => loadTodayView());
  }

  /* ── 命令面板 ── */
  const CMD_ITEMS = [
    { id: 'today', label: '打开 · 今天', view: 'today' },
    { id: 'ask', label: '打开 · 问答', view: 'ask' },
    { id: 'timeline', label: '打开 · 足迹', view: 'timeline' },
    { id: 'ops', label: '打开 · 设置', view: 'ops' },
    { id: 'backup', label: '备份数据库', action: 'backup' },
    { id: 'doctor', label: '系统检查', action: 'doctor' },
    { id: 'sync', label: '一键同步', action: 'sync' },
  ];

  function openCmdPalette() {
    const ov = $('#cmdPalette');
    if (!ov) return;
    ov.hidden = false;
    const inp = $('#cmdPaletteInput');
    if (inp) {
      inp.value = '';
      inp.focus();
      renderCmdResults('');
    }
  }

  function closeCmdPalette() {
    const ov = $('#cmdPalette');
    if (ov) ov.hidden = true;
  }

  async function runCmdAction(action) {
    closeCmdPalette();
    if (action === 'backup') {
      await g.api('/api/ops/backup', { method: 'POST' });
      g.switchView('ops');
    } else if (action === 'doctor') {
      g.switchView('acceptance');
      if (typeof g.runShipDoctor === 'function') await g.runShipDoctor();
    } else if (action === 'sync') {
      await g.api('/api/ops/sync', { method: 'POST' });
      g.switchView('ops');
    }
  }

  function renderCmdResults(q) {
    const list = $('#cmdPaletteList');
    if (!list) return;
    const qq = (q || '').trim().toLowerCase();
    const items = CMD_ITEMS.filter((it) => !qq || it.label.toLowerCase().includes(qq));
    list.innerHTML = items
      .map(
        (it, i) =>
          `<button type="button" class="cmd-item${i === 0 ? ' active' : ''}" data-id="${esc(it.id)}" data-view="${esc(it.view || '')}" data-action="${esc(it.action || '')}">${esc(it.label)}</button>`,
      )
      .join('');
    list.querySelectorAll('.cmd-item').forEach((btn) => {
      btn.onclick = async () => {
        if (btn.dataset.view) g.switchView(btn.dataset.view);
        else if (btn.dataset.action) await runCmdAction(btn.dataset.action);
        closeCmdPalette();
      };
    });
  }

  function initCmdPalette() {
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        openCmdPalette();
      }
      if (e.key === 'Escape') closeCmdPalette();
    });
    $('#cmdPaletteInput')?.addEventListener('input', (e) => renderCmdResults(e.target.value));
    $('#cmdPaletteOverlay')?.addEventListener('click', closeCmdPalette);
  }

  /* ── 首次向导 ── */
  let onboardingStep = 0;

  function showOnboarding(show) {
    const el = $('#onboardingWizard');
    if (el) el.hidden = !show;
  }

  async function finishOnboarding(skip) {
    showOnboarding(false);
    if (!skip) {
      await g.api('/api/ui-tier', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier: 'daily', onboarding_done: true, landing_view: 'today' }),
      });
      logUiEvent('onboarding_complete', { detail: 'wizard' });
      await loadUiTier();
    } else {
      await g.api('/api/ui-tier', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ onboarding_done: true }),
      });
      logUiEvent('onboarding_skip', {});
    }
    g.switchView(landingView || 'today');
  }

  function renderOnboardingStep() {
    const body = $('#onboardingBody');
    const steps = [
      '欢迎使用 QR 知识库。数据保存在 ~/.qr，代码在 ~/QR。',
      '建议开启 zsh 带时间戳的历史（qr shell check），时间线更准确。',
      '安装后台任务后可自动采集与索引（launchd）。',
      '点击「完成」运行首次同步并进入「今日」首页。',
    ];
    if (body) body.textContent = steps[onboardingStep] || steps[0];
    const dots = $('#onboardingDots');
    if (dots) {
      dots.innerHTML = steps.map((_, i) => `<span class="ob-dot${i === onboardingStep ? ' on' : ''}"></span>`).join('');
    }
  }

  function initOnboarding() {
    $('#onboardingNext')?.addEventListener('click', async () => {
      if (onboardingStep < 3) {
        onboardingStep += 1;
        renderOnboardingStep();
        if (onboardingStep === 3) {
          try {
            await g.api('/api/ops/schedule/install', { method: 'POST' });
          } catch (_e) {}
        }
        return;
      }
      try {
        await g.api('/api/ops/sync', { method: 'POST' });
      } catch (_e) {}
      await finishOnboarding(false);
    });
    $('#onboardingSkip')?.addEventListener('click', () => finishOnboarding(true));
    renderOnboardingStep();
  }

  async function maybeShowOnboarding(ui) {
    if (ui && ui.ui_onboarding_done === false && ui.ui_tier !== 'full') {
      showOnboarding(true);
    }
  }

  function logUiEvent(event, fields) {
    fields = fields || {};
    g.api('/api/ui-event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, view: fields.view || '', detail: fields.detail || '' }),
    }).catch(() => {});
  }

  function wrapSwitchView() {
    const orig = g.switchView;
    if (typeof orig !== 'function') return;
    g.switchView = function (v) {
      orig(v);
      syncNavActive(v);
      logUiEvent('view_switch', { view: v });
      if (v === 'today') loadTodayView();
    };
  }

  async function bootNav() {
    const ui = await loadUiTier();
    wrapSwitchView();
    bindSimpleNav();
    bindTopFocusBar();
    bindTodayShortcuts();
    initCmdPalette();
    initOnboarding();
    await loadTopFocusBar();
    const lv = landingView || 'ask';
    if (typeof g.switchView === 'function') g.switchView(lv);
    await maybeShowOnboarding(ui);
  }

  initTierInteraction();

  g.QrNav = {
    NAV_GROUPS,
    VIEW_TO_GROUP,
    applyUiTier,
    loadUiTier,
    loadTodayView,
    renderAlertQueue,
    renderHealthSimple,
    renderResumeBlock,
    syncNavActive,
    bootNav,
    openCmdPalette,
    logUiEvent,
    setUiTier,
    goAcceptanceView,
  };
  g.qrSetUiTier = setUiTier;
  g.qrGoAcceptance = goAcceptanceView;
})(window);
