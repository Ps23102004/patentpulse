(function(root, factory){
  if (typeof module === 'object' && module.exports){
    module.exports = factory();
  } else {
    root.PatentPulseApi = factory();
  }
})(typeof self !== 'undefined' ? self : this, function(){
  "use strict";

  // Real fetch layer only. This module never touches mock data — a failed or
  // malformed response rejects, and the caller is responsible for rendering an
  // honest error state. See sections/scan.js: mock data is loaded through a
  // completely separate code path (the "sample scan" button), never through
  // this function's catch handler.
  function scan(query){
    // AbortSignal.timeout rejects the fetch (a normal AbortError) if the
    // backend hangs, so a dead server shows the honest error state below
    // instead of spinning forever.
    return fetch('/api/scan?q=' + encodeURIComponent(query), { signal: AbortSignal.timeout(120000) }).then(function(r){
      if (!r.ok) throw new Error('Scan service returned HTTP ' + r.status);
      return r.json();
    }).then(function(data){
      if (!data || typeof data !== 'object' || !Array.isArray(data.results)){
        throw new Error('Scan service returned an unexpected response shape');
      }
      return data;
    });
  }

  return { scan: scan };
});
