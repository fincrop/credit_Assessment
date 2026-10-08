"""Track D evaluation harness: validation without field visits.

Modules
-------
metrics        per-class precision/recall/F1 (Wilson CIs), balanced accuracy,
               macro F1, ECE, coverage at threshold, Cohen's kappa
area           Olofsson et al. (2014) / Stehman (2014) stratified estimators
d1_sampling    D1 independent image interpretation (sample, blind sheet,
               Sentinel-2 chips, label import + kappa)
d3_client      client-record intake, validation, unit normalisation, matching
d4_official    official-statistics consistency checks
sowing_checks  sowing validation without farmer dates
gates          section 8 release-gate report
run            command-line entry point (python -m evaluation.run ...)

Nothing in this package invents ground truth or official statistics. Where
data must come from people (interpreters, clients, official tables) the
package ships header-only templates in ``evaluation/reference/`` and reports
"not available" / "NOT MEASURED" until they are filled.
"""

__version__ = "0.1.0"
