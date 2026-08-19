(function(root, factory){
  if (typeof module === 'object' && module.exports){ module.exports = factory(); }
  else { root.PATENTPULSE_MOCK = factory(); }
})(typeof self !== 'undefined' ? self : this, function(){
  "use strict";
  // Sample data for local dev / "View a sample scan", matching the exact
  // GET /api/scan JSON contract. Only ever read by the explicit sample-scan
  // button handler in sections/scan.js — never by the live-fetch error path.
  //
  // This is real output captured from the working backend (not invented):
  //   cd patentpulse && ./.venv/bin/python -m patentpulse.cli scan \
  //     "a robot that cleans dust off rooftop solar panels" --json
  // Every patent below is a genuine granted US patent from
  // patentpulse/fixtures/sample_patents.json (hand-transcribed from its
  // public Google Patents page — see that file's `_meta` block), and every
  // "View filing" link points at that same patent's real Google Patents page.
  return {
    query: "a robot that cleans dust off rooftop solar panels",
    generated_at: "2026-08-19T05:27:28Z",
    scope: {
      source: "fixture_sample",
      snapshot_date_range: ["1999-09-28", "2020-10-06"],
      note: "DEMO DATA — not a live patent search. These 8 records are real, granted US patents, each transcribed by hand from its public Google Patents page and committed to this repository as a fixture. They were picked to exercise the ranking end to end, not to represent any technical field. Live search over EPO Open Patent Services switches on automatically once EPO_OPS_KEY and EPO_OPS_SECRET are set in the environment. Publication dates run 1999-09-28 to 2020-10-06; fetched 2026-08-19T05:19:37Z. This is a bounded set, not the full patent record — a patent outside it will not appear here no matter how relevant it is. recent_filing_count counts patents across the whole snapshot; top_cpc_codes are drawn only from the returned results. relevance_score is relative to the best match in this scan, not an absolute confidence."
    },
    results: [
      {
        patent_id: "US9130502B1",
        title: "Photovoltaic panel cleaning machine",
        abstract: "The photovoltaic panel cleaning machine is installed upon a linear array of photovoltaic (solar) panels, automatically operating periodically to remove any dust, debris, and/or light condensation from the panels to optimize their efficiency. The machine is driven back and forth along tracks extending along opposite edges of the panel array, and receives electrical power from a storage battery charged by the solar panel array. The machine includes an air compressor, a blower and nozzles that blow dust and debris from the solar panels, and two different roller brushes. Air is blown over the panels on the first pass, and the machine then reverses direction to apply a first roller to the panels for further debris removal on a second pass. A third pass is made using the air blower, and the direction reverses once again, the second roller being applied on the fourth pass to statically attract any remaining debris.",
        assignee: "King Fahd University of Petroleum and Minerals",
        grant_date: "2015-09-08",
        filing_date: "2014-12-15",
        cpc_codes: ["H02S40/10", "A46B13/001", "A46B15/0051", "A47L1/02", "B08B1/32"],
        relevance_score: 1.0,
        url: "https://patents.google.com/patent/US9130502B1/en"
      },
      {
        patent_id: "US10797636B2",
        title: "Waterless cleaning system and method for solar trackers using an autonomous robot",
        abstract: "A solar tracker waterless cleaning system for cleaning solar panels of a solar tracker being able to be positioned at a pre-determined angle, including a docking station and an autonomous robotic cleaner (ARC), the docking station coupled with an edge of the solar tracker, the ARC including at least one rechargeable power source, at least one cleaning cylinder and a controller, the cleaning cylinder including a plurality of fins which rotates for generating a directional air flow for pushing dirt off of the surface of the solar tracker without water, the controller including a motion sensor for determining an angle of the solar tracker and a heading of the ARC, the docking station including at least one electrical connector for recharging the rechargeable power source, the controller for controlling a cleaning process of the ARC and for transmitting and receiving signals to and from the ARC.",
        assignee: "Evermore United SA",
        grant_date: "2020-10-06",
        filing_date: "2019-07-26",
        cpc_codes: ["F24S30/425", "B08B1/165", "B08B1/30", "B08B1/32", "B08B1/34"],
        relevance_score: 0.8338,
        url: "https://patents.google.com/patent/US10797636B2/en"
      },
      {
        patent_id: "US8500918B1",
        title: "Solar panel cleaning system and method",
        abstract: "System and method for cleaning a solar row of solar panels. The solar row has an upper edge elevated from ground level more than a lower edge to provide an inclination of the solar row. A cleaning assembly operates to clean a surface of the solar panels. A support frame supports the cleaning assembly and enables the cleaning assembly to move (1) upwardly and downwardly in the width direction of the solar row, and (2) in the length direction of the solar row. Operation and movement of the cleaning assembly is controlled by a control unit to cause the cleaning assembly to clean a surface of the solar panels during downward movement of the cleaning assembly. The cleaning assembly is preferably not operative during its upward vertical movement. During the downward movement, the cleaning assembly removes dirt, debris and dust from the surface of the solar panels and generates an air stream to blow off the dirt, debris, and dust.",
        assignee: "Ecoppia Scientific Ltd",
        grant_date: "2013-08-06",
        filing_date: "2013-01-28",
        cpc_codes: ["B08B1/30", "A46B13/005", "A46B13/02", "B08B1/32", "B08B1/34"],
        relevance_score: 0.818,
        url: "https://patents.google.com/patent/US8500918B1/en"
      },
      {
        patent_id: "US9455665B1",
        title: "Solar tracker cleaning system and method",
        abstract: "A solar tracker waterless cleaning system for cleaning solar panels of solar trackers in at least one solar tracker row, each solar tracker having a length and a width and being able to be positioned at a pre-determined angle, the cleaning system including at least one waterless cleaning apparatus operable to clean a panel surface of the solar tracker row without using water, at least two rails positioned horizontally parallel to the solar tracker row, a support frame for supporting the cleaning apparatus and a controller coupled with the cleaning apparatus and with the support frame for moving the cleaning apparatus in the width direction and the length direction of the solar tracker row, the support frame moving over the rails and moving the cleaning apparatus in a width direction and a length direction of the solar tracker row while maintaining a pre-determined angle in the width direction of the solar trackers.",
        assignee: "Ecoppia Scientific Ltd",
        grant_date: "2016-09-27",
        filing_date: "2016-01-25",
        cpc_codes: ["H02S40/10", "F24S40/20", "H02J7/35", "H02S20/32", "H02S30/10"],
        relevance_score: 0.5692,
        url: "https://patents.google.com/patent/US9455665B1/en"
      },
      {
        patent_id: "US7663607B2",
        title: "Multipoint touchscreen",
        abstract: "A touch panel having a transparent capacitive sensing medium configured to detect multiple touches or near touches that occur at the same time and at distinct locations in the plane of the touch panel and to produce distinct signals representative of the location of the touches on the plane of the touch panel for each of the multiple touches is disclosed.",
        assignee: "Apple Inc",
        grant_date: "2010-02-16",
        filing_date: "2004-05-06",
        cpc_codes: ["G06F3/0414", "G06F3/04164", "G06F3/04166", "G06F3/044", "G06F3/0443"],
        relevance_score: 0.1561,
        url: "https://patents.google.com/patent/US7663607B2/en"
      }
    ],
    landscape_summary: {
      text: "This technical territory is occupied by systems and methods for automatically cleaning solar arrays, particularly those involving robots or specialized frames. Patents like US9130502B1 describe a cleaning machine that operates on a linear array of panels, while US10797636B2 and US9455665B1 detail waterless cleaning systems using autonomous robots (ARCs) for solar trackers, which show activity from companies like Ecoppia Scientific Ltd. The existing patents generally focus on solar trackers or inclined rows (such as US8500918B1), leaving potential room for innovation in the specific context of standard, fixed-orientation rooftop panels. Any differentiation or refinement of the cleaning mechanism or method, especially for non-tracker installations, could represent a distinct area of development.",
      recent_filing_count: 4,
      top_cpc_codes: ["B08B1/32", "H02S40/10", "B08B1/30", "B08B1/34", "A46B13/001"]
    },
    disclaimer: "Informational only, not legal advice. PatentPulse is a keyword scan over a bounded, recent snapshot of granted US patents — it is not a freedom-to-operate opinion and it does not prove your idea is novel. If your situation is complicated (you are about to file, raise, or ship), a real patent attorney is worth paying for."
  };
});
