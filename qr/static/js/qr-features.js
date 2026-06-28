/** QR Web 扩展：今日入口、变更简报、符号检索、API 路径收敛 */
(function () {
  const IDENT_RE = /^[\w.]{3,80}$/;

  function briefUrl(project) {
    const ui = project || '';
    return ui
      ? `/api/project/brief?project=${encodeURIComponent(ui)}`
      : '/api/project/brief?auto=1';
  }

  window.fetchProjectBrief = async function (project) {
    return api(briefUrl(project));
  };

  async function loadProjectChangelog() {
    const p = document.querySelector('#projectSelect')?.value;
    const el = document.querySelector('#projectChangelogOut');
    if (!p || !el) return;
    el.textContent = '生成中…';
    try {
      const r = await api(`/api/changelog?project=${encodeURIComponent(p)}&days=7`);
      el.textContent = r.content || r.error || '（无内容）';
    } catch (e) {
      el.textContent = '出错：' + (e.message || e);
    }
  }

  async function loadDesignerMetrics() {
    const el = document.querySelector('#designerMetricsOut');
    if (!el) return;
    try {
      const s = await api('/api/ai-assess/snapshot');
      const dm = s.designer_metrics || {};
      el.textContent =
        `设计者指标 · 近30天决策/对话 ${dm.decisions_30d ?? '—'}/${dm.cursor_events_30d ?? '—'}`
        + `（${dm.decision_to_cursor_pct ?? 0}%）`
        + ` · 合并引导语 ${dm.merged_guides ?? 0}`
        + ` · ship-check ${dm.ship_check_count ?? 0} 次`;
    } catch (_) {
      el.textContent = '';
    }
  }

  async function runShipDoctor() {
    const detail = document.querySelector('#shipDoctorDetail');
    if (!detail) return;
    detail.textContent = '检查中…';
    try {
      const r = await api('/api/ship-check');
      const doc = (r.steps || []).find((s) => s.id === 'doctor') || {};
      const lines = [doc.detail || '完成'];
      (doc.ok_items || []).slice(0, 6).forEach((x) => lines.push('✓ ' + x));
      (doc.issues || []).forEach((i) => {
        lines.push((i.level === 'error' ? '✗ ' : '! ') + (i.message || ''));
      });
      detail.textContent = lines.join('\n');
      loadDesignerMetrics();
    } catch (e) {
      detail.textContent = '检查失败：' + (e.message || e);
    }
  }

  async function openShipDecisionDraft() {
    try {
      const r = await api('/api/decision/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const input = document.querySelector('#noteInput');
      if (input) input.value = r.text || '';
      if (typeof switchView === 'function') switchView('timeline');
      input?.focus();
    } catch (e) {
      alert(e.message || e);
    }
  }

  document.querySelector('#shipDoctorBtn')?.addEventListener('click', runShipDoctor);
  document.querySelector('#shipDecisionBtn')?.addEventListener('click', openShipDecisionDraft);

  async function loadFocusProjectSelect() {
    const sel = document.querySelector('#focusProjectSelect');
    if (!sel) return;
    try {
      const r = await api('/api/focus-project');
      const cur = r.focus_project || '';
      const projects = r.projects || [];
      const opts = ['<option value="">（自动检测活跃项目）</option>'];
      projects.forEach((p) => {
        const escP = typeof esc === 'function' ? esc(p) : p;
        opts.push(
          `<option value="${escP}"${p === cur ? ' selected' : ''}>${escP}</option>`,
        );
      });
      sel.innerHTML = opts.join('');
      if (cur && !projects.includes(cur)) {
        const escC = typeof esc === 'function' ? esc(cur) : cur;
        sel.insertAdjacentHTML(
          'beforeend',
          `<option value="${escC}" selected>${escC}</option>`,
        );
      }
    } catch (_) {
      /* 忽略 */
    }
  }

  async function saveFocusProject(project) {
    await api('/api/focus-project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: project || '' }),
    });
    loadTodayPanel();
    loadDesignerMetrics();
  }

  document.querySelector('#focusProjectSelect')?.addEventListener('change', (e) => {
    saveFocusProject(e.target.value).catch((err) => alert(err.message || err));
  });

  async function loadTodayPanel() {
    const el = document.querySelector('#todayOut');
    if (!el) return;
    el.textContent = '加载中…';
    try {
      const d = await api('/api/today');
      renderInsightKpis(d);
      const prefix = d.pending_prefix_sessions ?? 0;
    const prefixDays = d.pending_prefix_days ?? 7;
    const lines = [
        '—— 接着干 ——',
        ...((d.resume?.actions) || []).map((a) => '· ' + a),
      ];
      if (prefix > 0) {
        lines.push(
          '',
          `—— 引导语前缀 ——`,
          `· 近 ${prefixDays} 天无前缀对话 ${prefix} 场（侧栏改为「执行- 主题」后可进收件箱）`,
        );
      }
      const otherCursor = d.resume?.cursor_topics_other || [];
      const otherGit = d.resume?.recent_git_other || [];
      if (otherCursor.length || otherGit.length) {
        lines.push('', '—— 其他项目（已折叠）——');
        otherCursor.forEach((t) => {
          lines.push(`· [${t.project || '?'}] ${(t.title || '').slice(0, 60)}`);
        });
        otherGit.forEach((g) => {
          lines.push(`· [${g.project || '?'}] Git: ${(g.title || '').slice(0, 50)}`);
        });
      }
      if (lines.length <= 1) lines.push('· 暂无建议');
      const preview = (d.digest_preview || '').trim();
      if (preview) {
        lines.push('', '—— 洞察摘要（预览）——', preview.slice(0, 600));
      }
      if ((d.alerts || []).length) {
        lines.push('', '—— 提醒摘要 ——');
        d.alerts.slice(0, 4).forEach((a) => {
          lines.push(`[${a.level || 'info'}] ${a.message || ''}`);
        });
      }
      el.textContent = lines.join('\n');
    } catch (e) {
      el.textContent = '出错：' + (e.message || e);
    }
  }

  function renderInsightKpis(d) {
    const proj = document.querySelector('#insightKpiProject');
    const inbox = document.querySelector('#insightKpiInbox');
    const alerts = document.querySelector('#insightKpiAlerts');
    const actions = document.querySelector('#insightKpiActions');
    if (!proj) return;
    const focus = d.focus_project || d.resume?.focus_project;
    proj.textContent = focus || d.active_project || '—';
    if (inbox) inbox.textContent = String(d.inbox_count ?? '—');
    const prefixEl = document.querySelector('#insightKpiPrefix');
    if (prefixEl) {
      const n = d.pending_prefix_sessions ?? 0;
      prefixEl.textContent = String(n);
      prefixEl.style.color = n > 0 ? 'var(--amber)' : '';
    }
    const shellEl = document.querySelector('#insightKpiShellTs');
    if (shellEl) {
      const st = d.shell_timestamp || {};
      const pct = st.file_pct;
      shellEl.textContent = pct != null ? `${pct}%` : '—';
      shellEl.title = st.file_total
        ? `历史 ${st.file_with_ts}/${st.file_total} 行带时间戳 · 近 ${st.days || 7} 天 ${st.window_commands || 0} 条`
        : 'zsh 历史带 epoch 占比';
      shellEl.style.color = pct != null && pct < 80 ? 'var(--amber)' : '';
    }
    if (alerts) alerts.textContent = String((d.alerts || []).length);
    const acts = (d.resume?.actions) || [];
    if (actions) {
      actions.textContent = acts.length
        ? acts.slice(0, 2).join(' · ').slice(0, 48)
        : '暂无建议';
    }
  }

  function switchInsightTab(name) {
    document.querySelectorAll('.insight-nav-btn').forEach((t) => {
      const on = t.dataset.insightTab === name;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.insight-panel').forEach((p) => {
      p.classList.toggle('active', p.dataset.insightPanel === name);
    });
  }

  function initInsightTabs() {
    document.querySelectorAll('.insight-nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchInsightTab(btn.dataset.insightTab || 'bench'));
    });
    const tabForBtn = {
      digestBtn: 'digest',
      digestNotifyBtn: 'digest',
      factsInsightBtn: 'digest',
      exportBtn: 'digest',
      graphBtn: 'graph',
      complianceBtn: 'quality',
      evalRunBtn: 'quality',
      evalBtn: 'quality',
      evalHistoryBtn: 'quality',
      evalRegBtn: 'quality',
      evalFixBtn: 'quality',
      evalPlanBtn: 'quality',
      evalExecBtn: 'quality',
      evalDecisionBtn: 'quality',
    };
    Object.entries(tabForBtn).forEach(([id, tab]) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', () => switchInsightTab(tab), true);
    });
  }

  window.switchInsightTab = switchInsightTab;

  async function copyPlanCommand(cmd, btn) {
    const text = (cmd || '').trim();
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      if (btn) {
        const prev = btn.textContent;
        btn.textContent = '已复制';
        setTimeout(() => {
          btn.textContent = prev;
        }, 1200);
      }
    } catch (e) {
      window.prompt('复制评测命令：', text);
    }
  }

  function renderDailyPlan(data) {
    const list = document.querySelector('#dailyPlanList');
    const meta = document.querySelector('#dailyPlanMeta');
    if (!list) return;
    const items = data?.items || [];
    if (meta) {
      const done = data?.done_count || 0;
      const total = data?.total || items.length;
      meta.textContent = `${data?.date || ''} · ${done}/${total} 已完成（含按月项 ${data?.month || ''}）`;
    }
    if (!items.length) {
      list.textContent = '暂无每日计划项。';
      return;
    }
    list.innerHTML = items
      .map((item) => {
        const id = esc(item.id || '');
        const checked = item.done ? ' checked' : '';
        const doneCls = item.done ? ' is-done' : '';
        const cmd = esc(item.command || '');
        const cadence = item.cadence === 'monthly' ? '每月' : '每日';
        const hint = item.hint
          ? `<div class="plan-hint">${cadence} · ${esc(item.hint)}</div>`
          : `<div class="plan-hint">${cadence}</div>`;
        return `<div class="daily-plan-item${doneCls}" data-plan-id="${id}">
          <input type="checkbox" id="plan-${id}" data-plan-toggle="${id}"${checked} aria-label="${esc(item.label || id)}">
          <div class="plan-body">
            <label class="plan-label" for="plan-${id}">${esc(item.label || id)}</label>
            <div class="plan-cmd">${cmd}</div>
            ${hint}
          </div>
          <div class="daily-plan-actions">
            <button type="button" class="btn btn-ghost btn-sm" data-plan-copy="${esc(item.command || '')}">复制命令</button>
          </div>
        </div>`;
      })
      .join('');
    list.querySelectorAll('[data-plan-toggle]').forEach((el) => {
      el.addEventListener('change', async () => {
        const pid = el.getAttribute('data-plan-toggle');
        try {
          const r = await api('/api/insight/daily-plan/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: pid, done: el.checked }),
          });
          renderDailyPlan(r);
        } catch (e) {
          el.checked = !el.checked;
          alert('更新失败：' + (e.message || e));
        }
      });
    });
    list.querySelectorAll('[data-plan-copy]').forEach((btn) => {
      btn.addEventListener('click', () => copyPlanCommand(btn.getAttribute('data-plan-copy'), btn));
    });
  }

  async function loadDailyPlan() {
    const list = document.querySelector('#dailyPlanList');
    if (!list) return;
    list.textContent = '加载中…';
    try {
      const d = await api('/api/insight/daily-plan');
      renderDailyPlan(d);
    } catch (e) {
      list.textContent = '加载失败：' + (e.message || e);
    }
  }

  async function loadInsightAlerts() {
    const el = document.querySelector('#insightAlertsOut');
    if (!el) return;
    try {
      const r = await api('/api/alerts');
      const items = r.alerts || [];
      if (!items.length) {
        el.textContent = '暂无主动提醒';
        el.classList.add('is-empty');
        const ak = document.querySelector('#insightKpiAlerts');
        if (ak) ak.textContent = '0';
        return;
      }
      el.classList.remove('is-empty');
      const ak = document.querySelector('#insightKpiAlerts');
      if (ak) ak.textContent = String(items.length);
      el.textContent = items
        .map((a) => `[${a.level || 'info'}] ${a.type || ''}: ${a.message || ''}`)
        .join('\n');
    } catch (e) {
      el.classList.remove('is-empty');
      el.textContent = '加载失败：' + (e.message || e);
    }
  }

  async function runQueryWithSymbol() {
    const q = document.querySelector('#qInput')?.value?.trim();
    if (!q) return;
    const symOnly = document.querySelector('#qSymbolChk')?.checked;
    const out = document.querySelector('#qOut');
    if (!out) return;
    if (typeof qrRunHas === 'function' && qrRunHas('query')) return;
    out.innerHTML = '<div class="card"><span class="spin"></span> 检索中…</div>';
    if (typeof qrRunStart === 'function') {
      qrRunStart({ id: 'query', label: symOnly ? '符号检索' : '语义检索', group: 'query' });
    }
    try {
      let hits = [];
      const project = document.querySelector('#qProject')?.value || null;
      const category = document.querySelector('#qCategory')?.value || null;
      if (symOnly || IDENT_RE.test(q)) {
        const sym = await api(
          `/api/symbol?name=${encodeURIComponent(q)}&limit=12` +
            (project ? `&project=${encodeURIComponent(project)}` : ''),
        );
        hits = (sym.hits || []).map((h) => ({
          path: h.path,
          project: h.project,
          score: 1,
          line: h.line,
          text: `[符号 ${h.kind}] ${h.name} — 第 ${h.line} 行`,
          source_type: 'symbol',
        }));
      }
      if (!symOnly) {
        const r = await api('/api/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: q, k: 8, project, category }),
        });
        const seen = new Set(hits.map((h) => h.path));
        (r.hits || []).forEach((h) => {
          if (!seen.has(h.path)) hits.push(h);
        });
      }
      if (!hits.length) {
        out.innerHTML =
          '<div class="empty"><div class="empty-icon">🔍</div>没有命中，先索引项目内容</div>';
        return;
      }
      out.innerHTML = `<div class="hits">${hits.map(hitCard).join('')}</div>`;
    } catch (e) {
      out.innerHTML = `<div class="card" style="color:var(--amber)">出错：${esc(e.message)}</div>`;
    } finally {
      if (typeof qrRunEnd === 'function') qrRunEnd('query');
    }
  }

  document.querySelector('#projectChangelogBtn')?.addEventListener('click', loadProjectChangelog);
  document.querySelector('#todayRefreshBtn')?.addEventListener('click', loadTodayPanel);
  document.querySelector('#insightAlertsBtn')?.addEventListener('click', loadInsightAlerts);
  initInsightTabs();

  const qBtn = document.querySelector('#qBtn');
  if (qBtn) qBtn.onclick = runQueryWithSymbol;

  const origSwitch = window.switchView;
  if (typeof origSwitch === 'function') {
    window.switchView = function (v) {
      origSwitch(v);
      if (v === 'insight') {
        loadDailyPlan();
        loadFocusProjectSelect();
        loadTodayPanel();
        loadInsightAlerts();
        loadDesignerMetrics();
      }
    };
  }

  window.loadTodayPanel = loadTodayPanel;
  window.loadDailyPlan = loadDailyPlan;
  window.loadProjectChangelog = loadProjectChangelog;
})();
