/** QR Web 核心：DOM / API / 打开文件 / 检索卡片 */
(function (g) {
  const QUERY_HIT_COLORS = [
    '#5eb3ff', '#5ddea8', '#ffc266', '#c9a0ff', '#ff8fb8',
    '#4dd4e8', '#ffe566', '#98e870', '#ff9ec0', '#7ec8ff',
  ];

  function $(s) {
    return document.querySelector(s);
  }
  function $$(s) {
    return document.querySelectorAll(s);
  }
  function esc(s) {
    return (s || '').replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  }
  async function api(p, o) {
    o = o || {};
    if (
      o.body &&
      typeof o.body === 'string' &&
      !(o.headers && (o.headers['Content-Type'] || o.headers['content-type']))
    ) {
      o.headers = { ...(o.headers || {}), 'Content-Type': 'application/json' };
    }
    let r;
    try {
      r = await fetch(p, o);
    } catch (e) {
      const msg = String(e && e.message ? e.message : e);
      if (/load failed|failed to fetch|networkerror/i.test(msg)) {
        throw new Error('无法连接本机 Web 服务（请求超时或服务重启中），请稍候重试');
      }
      throw e;
    }
    if (!r.ok) {
      let e;
      try {
        const j = await r.json();
        if (Array.isArray(j.detail)) {
          e = j.detail
            .map((d) => {
              const loc = (d.loc || []).filter((x) => x !== 'body').join('.');
              return d.msg + (loc ? ` (${loc})` : '');
            })
            .join('；');
        } else {
          e = j.error || j.detail;
        }
      } catch (_e) {
        e = r.statusText;
      }
      if (r.status === 404 && String(p).includes('/standards/activate')) {
        e =
          (e || '接口不存在') +
          ' — 请在本机执行 qr web --install 或重启 Web 服务以加载新代码';
      }
      throw new Error(e || '请求失败');
    }
    return r.json();
  }
  async function openLocalPath(path, line) {
    if (!path) return;
    const body = { path };
    if (line) body.line = Number(line);
    await api('/api/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  function bindOpenLinks(root) {
    if (!root || root._qrOpenBound) return;
    root._qrOpenBound = true;
    root.addEventListener('click', async (ev) => {
      const a = ev.target.closest('.tl-link');
      if (!a?.dataset.path) return;
      ev.preventDefault();
      try {
        await openLocalPath(a.dataset.path, a.dataset.line);
      } catch (e) {
        alert('无法打开：' + (e.message || e));
      }
    });
  }
  function scoreChips(x) {
    const s = x.scores || {};
    const chips = [
      ['final', x.score],
      ['vec', s.vector],
      ['fts', s.fts],
      ['rrf', s.rrf],
      ['boost', s.path_boost],
    ].filter(([, v]) => typeof v === 'number' && !Number.isNaN(v));
    if (!chips.length) return `<span class="hit-score">${(x.score || 0).toFixed(3)}</span>`;
    return `<div class="score-chips">${chips
      .map(([k, v]) => `<span class="score-chip">${k} ${v.toFixed(3)}</span>`)
      .join('')}</div>`;
  }
  function hitPathHtml(path, color, line) {
    if (!path) return '';
    const name = path.split('/').pop() || path;
    const lineAttr = line ? ` data-line="${line}"` : '';
    const title = line ? `${path}:${line}` : path;
    return `<a href="#" class="tl-link hit-path" data-path="${esc(path)}"${lineAttr} title="${esc(title)}" style="color:${color}">${esc(name)}</a>`;
  }
  function hitCard(x, i) {
    const c = QUERY_HIT_COLORS[i % QUERY_HIT_COLORS.length];
    const st = x.source_type || 'other';
    const lineHint =
      x.line && st === 'symbol'
        ? ` <a href="#" class="tl-link tag src" data-path="${esc(x.path)}" data-line="${x.line}" style="border-color:${c}55;color:${c}">L${x.line}</a>`
        : '';
    return `<div class="hit" style="border-color:${c}44;border-left-color:${c};background:linear-gradient(135deg,${c}28,${c}0c)"><div class="hit-top"><span class="hit-rank" style="background:${c};color:#0a0e14">${i + 1}</span>${hitPathHtml(x.path, c, x.line)}${lineHint}${x.project ? `<span class="tag proj">${esc(x.project)}</span>` : ''}<span class="tag src" style="border-color:${c}55;color:${c}">${esc(st)}</span></div>${scoreChips(x)}<pre style="color:var(--text2)">${esc((x.text || '').slice(0, 500))}</pre></div>`;
  }
  function askRefsHtml(hits, web) {
    if ((!hits || !hits.length) && (!web || !web.length)) return '';
    let h = '<div class="refs">';
    if (hits && hits.length) {
      h += `<div class="ref-group"><b>本地</b> · ${hits
        .map((x, i) => {
          const sc = x.scores || {};
          const extra =
            sc.rrf != null
              ? ` v${(sc.vector || 0).toFixed(2)} f${(sc.fts || 0).toFixed(2)} b${(sc.path_boost || 0).toFixed(2)}`
              : '';
          const fname = x.path ? x.path.split('/').pop() : '?';
          const lineAttr = x.line ? ` data-line="${x.line}"` : '';
          const label = x.path
            ? `<a href="#" class="tl-link" data-path="${esc(x.path)}"${lineAttr}>${esc(fname)}${x.line ? ':L' + x.line : ''}</a>`
            : esc(fname);
          return `${i + 1}. ${label}${x.source_type ? '[' + esc(x.source_type) + ']' : ''} (${(x.score || 0).toFixed(2)}${extra})`;
        })
        .join(' · ')}</div>`;
    }
    if (web && web.length) {
      h += `<div class="ref-group"><b>网络</b> · ${web
        .map((x, i) => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${i + 1}. ${esc(x.title.slice(0, 30))}</a>`)
        .join(' · ')}</div>`;
    }
    return h + '</div>';
  }

  g.QR = {
    $,
    $$,
    esc,
    api,
    openLocalPath,
    bindOpenLinks,
    scoreChips,
    hitPathHtml,
    hitCard,
    askRefsHtml,
    QUERY_HIT_COLORS,
  };
  g.$ = $;
  g.$$ = $$;
  g.esc = esc;
  g.api = api;
  g.openLocalPath = openLocalPath;
  g.bindOpenLinks = bindOpenLinks;
  g.scoreChips = scoreChips;
  g.hitPathHtml = hitPathHtml;
  g.hitCard = hitCard;
  g.askRefsHtml = askRefsHtml;
  g.QUERY_HIT_COLORS = QUERY_HIT_COLORS;
})(window);
