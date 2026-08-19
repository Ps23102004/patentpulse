(function(){
  "use strict";

  function esc(s){
    var d = document.createElement('div');
    d.textContent = String(s);
    // textContent->innerHTML escapes & < > but not quotes; escape those too
    // since esc() is also used inside HTML attribute values below.
    return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  // Only http(s) links are ever rendered as hrefs — API-supplied data could
  // otherwise smuggle a javascript: URI into an <a> tag.
  function safeUrl(u){
    try { var p = new URL(String(u), location.href); return (p.protocol === 'http:' || p.protocol === 'https:') ? p.href : '#'; }
    catch (e){ return '#'; }
  }
  function fmtDate(s){
    if (!s) return "unknown";
    var d = new Date(s + "T00:00:00Z");
    if (isNaN(d.getTime())) return s;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
  }

  var SEAL_R = 22;
  var SEAL_C = 2 * Math.PI * SEAL_R;

  // Relative indicator only: the backend normalizes the top hit in every scan
  // to exactly 1.0, so a percentage or numeric label would always show the
  // #1 result as "100%" regardless of how good a match it actually is. The
  // ring's fill length is the only signal — relative to this scan's other
  // results, not an absolute confidence.
  function sealSvg(score){
    var frac = Math.max(0, Math.min(1, Number(score) || 0));
    var dash = (frac * SEAL_C).toFixed(1) + ' ' + SEAL_C.toFixed(1);
    return '<div class="seal" aria-label="Relative match strength within this scan">' +
      '<svg viewBox="0 0 52 52">' +
        '<circle class="seal-track" cx="26" cy="26" r="' + SEAL_R + '"></circle>' +
        '<circle class="seal-fill" cx="26" cy="26" r="' + SEAL_R + '" stroke-dasharray="' + dash + '"></circle>' +
      '</svg>' +
    '</div>';
  }

  // Tally CPC code frequency across the returned results so the chart reflects
  // exactly what's on screen, rather than a separately-fetched breakdown.
  function tallyCpc(results){
    var counts = {};
    results.forEach(function(r){
      (r.cpc_codes || []).forEach(function(code){ counts[code] = (counts[code] || 0) + 1; });
    });
    return Object.keys(counts).map(function(code){ return { code: code, count: counts[code] }; })
      .sort(function(a, b){ return b.count - a.count || a.code.localeCompare(b.code); })
      .slice(0, 6);
  }

  function cpcChart(results, topCodes){
    var rows = tallyCpc(results);
    if (!rows.length) return '';
    var max = rows[0].count;
    var topSet = {};
    (topCodes || []).forEach(function(c){ topSet[c] = true; });
    return '<div class="cpc-chart" role="img" aria-label="Distribution of CPC classification codes across the results shown">' +
      rows.map(function(row){
        var widthPct = Math.max(6, Math.round((row.count / max) * 100));
        return '<div class="cpc-row">' +
          '<span class="cpc-code mono">' + esc(row.code) + (topSet[row.code] ? ' *' : '') + '</span>' +
          '<span class="cpc-track"><span class="cpc-fill" style="width:' + widthPct + '%"></span></span>' +
          '<span class="cpc-count">' + row.count + '</span>' +
        '</div>';
      }).join('') +
    '</div>';
  }

  function resultCard(r, index){
    var cpcChips = (r.cpc_codes || []).map(function(c){ return '<span class="chip mono">' + esc(c) + '</span>'; }).join('');
    var detailId = 'detail-' + index;
    return '<li class="result-card glass" style="animation-delay:' + (index * 70) + 'ms">' +
      '<div class="card-top">' +
        '<div class="card-title-group">' +
          '<h3 class="result-title display">' + esc(r.title) + '</h3>' +
          '<p class="result-meta">' +
            '<span class="pid mono">' + esc(r.patent_id) + '</span>' +
            '<span>' + esc(r.assignee || 'Assignee not listed') + '</span>' +
            '<span>Granted ' + esc(fmtDate(r.grant_date)) + '</span>' +
          '</p>' +
        '</div>' +
        sealSvg(r.relevance_score) +
      '</div>' +
      '<div class="card-cpc chip-row">' + cpcChips + '</div>' +
      '<div class="card-actions">' +
        '<button class="detail-btn" type="button" data-toggle="' + detailId + '" aria-expanded="false" aria-controls="' + detailId + '">Show details</button>' +
        '<a class="result-link" href="' + esc(safeUrl(r.url)) + '" target="_blank" rel="noopener noreferrer">View filing &#8599;</a>' +
      '</div>' +
      '<div class="detail-collapse" id="' + detailId + '">' +
        '<div class="detail-collapse-inner"><div class="detail-body">' +
          '<p class="result-abstract">' + esc(r.abstract) + '</p>' +
          '<p class="result-meta" style="margin-top:12px">' +
            '<span>Filed ' + esc(fmtDate(r.filing_date)) + '</span>' +
          '</p>' +
        '</div></div>' +
      '</div>' +
    '</li>';
  }

  function mockBanner(){
    return '<div class="banner banner-mock glass">' +
      '<strong>Sample data.</strong> This is an illustrative scan, not a live result — use it to preview the layout before the scan service is reachable.' +
    '</div>';
  }

  function scopeBanner(scope){
    return '<div class="banner banner-scope glass">' +
      '<strong>Scope:</strong> ' + esc(scope.note) +
      ' <span class="mono">(' + esc(scope.snapshot_date_range[0]) + ' &ndash; ' + esc(scope.snapshot_date_range[1]) + ')</span>' +
    '</div>';
  }

  function renderScan(container, data, opts){
    opts = opts || {};
    var summary = data.landscape_summary;
    var topChips = (summary.top_cpc_codes || []).map(function(c){ return '<span class="chip top mono">' + esc(c) + '</span>'; }).join('');

    container.innerHTML =
      '<div class="section-stack" style="display:flex;flex-direction:column;gap:20px">' +
        (opts.mock ? mockBanner() : '') +
        scopeBanner(data.scope) +
        '<div class="summary-card glass fade-in">' +
          '<h2>Landscape summary</h2>' +
          '<p class="summary-text">' + esc(summary.text) + '</p>' +
          '<div class="summary-stats">' +
            '<div class="summary-stat"><strong>' + data.results.length + '</strong><span>Results returned</span></div>' +
            '<div class="summary-stat"><strong>' + summary.recent_filing_count + '</strong><span>Recent filings</span></div>' +
          '</div>' +
          '<div>' +
            '<p class="summary-text" style="margin-bottom:8px"><strong style="color:var(--ink)">Top classifications</strong></p>' +
            '<div class="chip-row">' + topChips + '</div>' +
          '</div>' +
          '<div>' +
            '<p class="summary-text" style="margin-bottom:8px"><strong style="color:var(--ink)">CPC distribution across results</strong> <span style="font-size:12px">(* = top classification)</span></p>' +
            cpcChart(data.results, summary.top_cpc_codes) +
          '</div>' +
        '</div>' +
        '<ul class="result-list">' + data.results.map(resultCard).join('') + '</ul>' +
        '<div class="disclaimer glass"><strong>Disclaimer.</strong> ' + esc(data.disclaimer) + '</div>' +
      '</div>';

    container.querySelectorAll('[data-toggle]').forEach(function(btn){
      btn.addEventListener('click', function(){
        var panel = document.getElementById(btn.getAttribute('data-toggle'));
        var open = panel.classList.toggle('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.textContent = open ? 'Hide details' : 'Show details';
      });
    });
  }

  function renderLoading(container){
    container.innerHTML = '<div class="state-panel glass fade-in">' +
      '<div class="spinner-ring" aria-hidden="true"></div>' +
      '<h2>Scanning the patent record&hellip;</h2>' +
      '<p>Comparing your description against the snapshot. This can take a few seconds.</p>' +
    '</div>';
  }

  function renderError(container, err, onRetry){
    container.innerHTML = '<div class="state-panel glass error fade-in">' +
      '<h2>Scan failed</h2>' +
      '<p>The scan service couldn&rsquo;t be reached or returned something unexpected: <span class="mono">' + esc(err && err.message ? err.message : 'unknown error') + '</span>. No results to show &mdash; this is not sample data.</p>' +
      '<button class="go" type="button" id="retry-btn">Try again</button>' +
    '</div>';
    var btn = container.querySelector('#retry-btn');
    if (btn) btn.addEventListener('click', onRetry);
  }

  document.addEventListener('DOMContentLoaded', function(){
    var results = document.getElementById('scan-results');
    var form = document.getElementById('scan-form');
    var input = document.getElementById('scan-query');
    var submitBtn = form.querySelector('button[type="submit"]');
    var sampleBtn = document.getElementById('sample-btn');

    // Monotonic scan id: every scan (live fetch or sample button) claims the
    // next id before it starts. Whichever one resolves must check it's still
    // the current id before touching the DOM — otherwise an old in-flight
    // fetch resolving after a newer scan started (or after "View a sample
    // scan" was clicked) can silently overwrite newer/sample results with
    // stale ones.
    var scanId = 0;

    function runScan(query){
      var id = ++scanId;
      submitBtn.disabled = true;
      renderLoading(results);
      PatentPulseApi.scan(query).then(function(data){
        if (id !== scanId) return;
        renderScan(results, data, { mock: false });
      }).catch(function(err){
        if (id !== scanId) return;
        // Live fetch failed: show an honest error state. Mock data is never
        // reachable from this path — PATENTPULSE_MOCK is only read below, from
        // the sample-scan button's own handler.
        renderError(results, err, function(){ runScan(query); });
      }).then(function(){
        if (id === scanId) submitBtn.disabled = false;
      });
    }

    form.addEventListener('submit', function(e){
      e.preventDefault();
      var query = input.value.trim();
      if (!query) return;
      runScan(query);
    });

    sampleBtn.addEventListener('click', function(){
      scanId++;
      renderScan(results, window.PATENTPULSE_MOCK, { mock: true });
      input.value = window.PATENTPULSE_MOCK.query;
      // Always re-enable: this click may "win" the race against an in-flight
      // live scan, whose own cleanup then sees a stale scanId and skips
      // re-enabling submitBtn, wedging the form shut. Re-enabling here is
      // unconditional regardless of which action completes last.
      submitBtn.disabled = false;
    });

    document.addEventListener('mousemove', function(e){
      document.documentElement.style.setProperty('--mx', e.clientX + 'px');
      document.documentElement.style.setProperty('--my', e.clientY + 'px');
    }, { passive: true });
  });
})();
