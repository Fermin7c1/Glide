# Test-suite performance investigation (2026-11-02)

Working notes for the sessions implementing the speedups. NOT for main. Each `<area>.md` is one deep dive: where the
time goes (profiled), the proposals with measured before/after, and the skeptics' verdicts where they ran
(`challenge (cov)` = coverage/semantics, `challenge (gain)` = re-measured gain). `<area>__<proposal>.diff` is the
dive's prototype as a unified diff against origin/main 6176835 (main has moved since: rebase/re-apply by hand where it
doesn't apply). `baseline_run_all_j4.json`: per-test wall/CPU of a full run at -j4 on a 4-CPU Linux box (67.9 s total).
