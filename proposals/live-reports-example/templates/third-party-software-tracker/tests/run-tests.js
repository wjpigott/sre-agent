#!/usr/bin/env node
// Offline tests for report/third-party-software-tracker.html.
// Runs the report script in a Node VM with a minimal DOM stub and a mocked
// window.sreagent.callTool, then checks classification, parsing of the
// different tool-response shapes, and the error/fallback paths.
// Usage: node tests/run-tests.js   (Node 18+; no npm dependencies)
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'report', 'third-party-software-tracker.html'), 'utf8');
const rows = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'sample-inventory.json'), 'utf8'));

// ---------- static checks ----------
const failures = [];
function check(name, cond, detail) {
  if (cond) console.log('  PASS ' + name);
  else { console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')); failures.push(name); }
}

console.log('Static checks');
const scripts = html.match(/<script nonce="\{REPORT_NONCE\}">/g) || [];
check('exactly one inline script, nonce placeholder intact', scripts.length === 1);
check('style tag carries nonce placeholder', html.includes('<style nonce="{REPORT_NONCE}">'));
check('no inline event handlers (blocked by CSP)', !/\son[a-z]+\s*=\s*["']/i.test(html.replace(/<script[\s\S]*?<\/script>/g, '')));
check('CDN scripts are SRI-pinned', (html.match(/<script src=/g) || []).length === (html.match(/<script src=[^>]*integrity=/g) || []).length);
check('template ships an empty snapshot', /const SNAPSHOT = \[\];/.test(html));
check('template keeps CONFIG placeholders', html.includes("'<SUBSCRIPTION_ID>'") && html.includes("'<WORKSPACE_CUSTOMER_ID>'"));
check('only the allowed tool is called', /const LOG_QUERY_TOOL = 'system-mcp-monitor_monitor_workspace_log_query';/.test(html));

const kql = fs.readFileSync(path.join(ROOT, 'queries', 'report-inventory.kql'), 'utf8');
const scriptSrc = html.split('<script nonce="{REPORT_NONCE}">')[1].split('</script>')[0];
const norm = s => s.replace(/\/\/.*$/gm, '').replace(/\s+/g, ' ').trim();
const qInHtml = (scriptSrc.match(/query: \[([\s\S]*?)\]\.join\(' '\)/) || [])[1] || '';
const qFromHtml = norm(qInHtml.split(/',\s*'/).join(' ').replace(/^\s*'|'\s*$/g, ''));
check('report query matches queries/report-inventory.kql', qFromHtml === norm(kql), '\n    html: ' + qFromHtml + '\n    kql:  ' + norm(kql));

// ---------- runtime harness ----------
function runReport(mode, opts) {
  opts = opts || {};
  let src = scriptSrc;
  if (!opts.keepPlaceholders) {
    src = src.replace("'<SUBSCRIPTION_ID>'", "'00000000-0000-0000-0000-000000000000'")
             .replace("'<WORKSPACE_RESOURCE_GROUP>'", "'rg-test'")
             .replace("'<WORKSPACE_CUSTOMER_ID>'", "'11111111-1111-1111-1111-111111111111'");
  }
  const data = opts.rows || rows;
  const els = {};
  const mkEl = id => {
    const e = {
      id, children: [], style: {}, textContent: '', _html: '',
      classList: { add() {}, toggle() {} },
      appendChild(c) { this.children.push(c); return c; },
      querySelector() { return mkEl('q'); },
      getContext() { return {}; },
      parentElement: { style: {}, innerHTML: '', appendChild() {} }
    };
    Object.defineProperty(e, 'innerHTML', { get() { return this._html; }, set(v) { this._html = v; if (v === '') this.children = []; } });
    return e;
  };
  const state = { grid: null, chart: null, args: null };
  const body = { status: 200, message: 'Success', results: data };
  const responses = {
    object: () => body,
    string: () => JSON.stringify(body),
    double: () => JSON.stringify(JSON.stringify(body)),
    strstatus: () => JSON.stringify(Object.assign({}, body, { status: '200' })),
    mcp: () => ({ content: [{ type: 'text', text: JSON.stringify(body) }] }),
    mcpstr: () => JSON.stringify({ content: [{ type: 'text', text: JSON.stringify(body) }] }),
    tables: () => ({ tables: [{ columns: Object.keys(data[0]).map(n => ({ name: n })), rows: data.map(r => Object.values(r)) }] }),
    err500: () => JSON.stringify({ status: 500, message: 'Semantic error', results: [] }),
    empty: () => JSON.stringify({ status: 200, message: 'Success', results: [] }),
    reject: () => { throw new Error('tool timeout'); },
    garbage: () => 'not json'
  };
  const ctx = {
    console, setTimeout, Date, JSON, Math, String, Object, Array, Map, Set, Number, parseInt, isNaN, Error, Promise, RegExp,
    document: {
      getElementById: id => (els[id] = els[id] || mkEl(id)),
      createElement: t => mkEl(t),
      createTextNode: t => ({ textContent: t }),
      querySelectorAll: () => []
    },
    gridjs: { Grid: function (cfg) { state.grid = cfg.data; this.render = () => {}; }, html: s => s },
    Chart: Object.assign(function (c, cfg) { state.chart = cfg; }, { register() {} }),
    ChartDataLabels: {},
    window: { sreagent: { callTool: async (name, args) => { state.args = args; state.tool = name; return responses[mode](); } } }
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return new Promise(resolve => setTimeout(() => {
    const t = id => (els[id] || {}).textContent;
    const pc = els.priorityContent;
    resolve({
      total: t('totalCount'), current: t('currentCount'), outdated: t('outdatedCount'),
      critical: t('criticalCount'), untracked: t('untrackedCount'), source: t('dataSource'),
      banners: ((els.banners && els.banners.children) || []).map(b => b.textContent),
      error: pc && pc.children[0] && pc.children[0].className === 'error-box' ? pc.children[0].textContent : null,
      grid: state.grid, args: state.args, tool: state.tool
    });
  }, 50));
}

(async () => {
  // Expected for fixture: 12 entries after collapsing 3 Python component rows to 1
  const EXP = { total: '12', current: '2', outdated: '8', critical: '2', untracked: '1' };

  for (const mode of ['object', 'string', 'double', 'strstatus', 'mcp', 'mcpstr', 'tables']) {
    console.log('Response shape: ' + mode);
    const r = await runReport(mode);
    check(mode + ' parsed as LIVE', r.source === 'Data: LIVE', r.source + ' ' + JSON.stringify(r.banners));
    check(mode + ' summary counts', r.total === EXP.total && r.current === EXP.current && r.outdated === EXP.outdated &&
      r.critical === EXP.critical && r.untracked === EXP.untracked, JSON.stringify(r));
  }

  console.log('Classification details');
  const r = await runReport('string');
  const row = (c, n) => (r.grid || []).find(g => g[1] === c && String(g[2]).startsWith(n));
  check('Chrome .80 is Current (dual Windows build)', row('SRV01', 'Google Chrome')[0] === 'current');
  check('Chrome patch lag is Medium', row('SRV02', 'Google Chrome')[0] === 'medium');
  check('Chrome 2+ majors behind is High', row('SRV03', 'Google Chrome')[0] === 'high');
  check('7-Zip 26.04 Current, 26.03 Medium', row('SRV02', '7-Zip')[0] === 'current' && row('SRV01', '7-Zip')[0] === 'medium');
  check('Java 8u471 Critical, shown as 8u471', row('SRV03', 'Java 8')[0] === 'critical' && row('SRV03', 'Java 8')[3] === '8u471');
  check('Java Auto Updater compared on JRE update number', row('SRV03', 'Java Auto Updater')[0] === 'low');
  check('Python components collapsed to one row', (r.grid || []).filter(g => g[1] === 'SRV04' && /^Python/.test(g[2])).length === 1);
  check('Untracked product listed as unknown', row('SRV05', 'VMware Tools')[0] === 'unknown');
  check('no stale-reference banner for fixture', !r.banners.some(b => b.startsWith('Reference may be stale')));
  check('tool call uses CONFIG values', r.tool === 'system-mcp-monitor_monitor_workspace_log_query' && r.args['resource-group'] === 'rg-test' && r.args.limit === 1000);

  console.log('Stale reference detection');
  const newer = rows.map(x => x.SoftwareName.startsWith('7-Zip 26.04') ? Object.assign({}, x, { SoftwareName: '7-Zip 26.05 (x64)', CurrentVersion: '26.05' }) : x);
  const rs = await runReport('string', { rows: newer });
  check('newer-than-reference version raises banner', rs.banners.some(b => b.includes('7-Zip 26.05')));

  console.log('Failure paths (no snapshot in template)');
  for (const mode of ['err500', 'empty', 'reject', 'garbage']) {
    const f = await runReport(mode);
    check(mode + ' shows error box', !!f.error && f.source === 'Data: UNAVAILABLE', JSON.stringify(f.error));
  }
  const p = await runReport('string', { keepPlaceholders: true });
  check('placeholder CONFIG is rejected before calling the tool', !!p.error && /placeholder/.test(p.error) && !p.args);

  console.log('');
  if (failures.length) { console.log(failures.length + ' check(s) failed'); process.exit(1); }
  console.log('All checks passed');
})().catch(e => { console.error(e); process.exit(1); });
