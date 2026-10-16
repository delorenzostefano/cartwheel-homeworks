"""Bias-corrected failure prevalence for a monitoring period."""

from __future__ import annotations

from typing import Any, Sequence


def corrected_mode_prevalence(
    sample_preds: Sequence[int],
    test_labels: Sequence[int],
    test_preds: Sequence[int],
    confidence: float = 0.95,
    bootstrap_iterations: int = 20000,
    seed: int | None = 7,
) -> dict[str, Any]:
    """Bias-corrected live prevalence for one mode from sampled verdicts.

    The contract, precisely:

      1. ``raw`` is the uncorrected flag rate: ``mean(sample_preds)``.
      2. Compute the frozen judge's failure sensitivity and pass specificity
         from ``test_labels`` and ``test_preds``. Both use the monitoring
         convention that 1 means a failure is present. Failure sensitivity is
         the flagged fraction of human-labeled failures. Pass specificity is
         the unflagged fraction of human-labeled passes.
      3. Compute the Rogan-Gladen point estimate, then resample the held-out
         records and sampled predictions to obtain a percentile-bootstrap
         interval. Use a seeded NumPy generator so the committed result is
         reproducible.
      4. Resample the monitoring predictions and the paired held-out records
         independently with replacement. Keep their original sample sizes.
         Discard a draw if the correction cannot be computed. Clamp each
         retained estimate to [0, 1], then take the percentile interval.
         Raise ``ValueError`` if no replicate is valid.

    Args:
        sample_preds: the judge's 0/1 verdicts over the UNIFORM BASE sample
            only (never the risk strata; they are biased toward failure by
            design).
        test_labels: human labels for the frozen Homework 5 judge's test
            split.
        test_preds: the frozen judge's predictions on that test split.
        confidence: interval confidence level.
        bootstrap_iterations: number of percentile-bootstrap replicates.
        seed: numpy seed for a reproducible interval; None leaves the RNG
            untouched.

    Returns:
        {"raw", "corrected", "ci_low", "ci_high", "confidence",
         "failure_sensitivity", "pass_specificity", "n_sample"}
        with "corrected" clamped to [0, 1] and rates rounded to 4 places.

    Raises:
        ValueError: if an input is empty, the held-out inputs have different
            lengths, a value is not 0 or 1, a class is absent, the judge is
            missing a usable correction, or no bootstrap replicate is valid.
    """
    import numpy as np

    sample = np.asarray(sample_preds, dtype=int)
    labels = np.asarray(test_labels, dtype=int)
    preds = np.asarray(test_preds, dtype=int)
    if sample.size == 0 or labels.size == 0:
        raise ValueError("sample and held-out inputs must be nonempty")
    if labels.size != preds.size:
        raise ValueError("held-out labels and predictions differ in length")
    for array in (sample, labels, preds):
        if not np.isin(array, (0, 1)).all():
            raise ValueError("every value must be 0 or 1")
    if labels.sum() == 0 or labels.sum() == labels.size:
        raise ValueError("held-out labels need both failures and passes")

    def rates(lab: np.ndarray, pre: np.ndarray) -> tuple[float, float] | None:
        failures, passes = lab == 1, lab == 0
        if not failures.any() or not passes.any():
            return None
        sensitivity = float(pre[failures].mean())         # flagged human failures
        specificity = float(1 - pre[passes].mean())       # unflagged human passes
        return sensitivity, specificity

    def rogan_gladen(raw: float, sens: float, spec: float) -> float | None:
        denominator = sens + spec - 1
        if abs(denominator) < 1e-12:
            return None
        return (raw + spec - 1) / denominator

    raw = float(sample.mean())
    sens, spec = rates(labels, preds)
    point = rogan_gladen(raw, sens, spec)
    if point is None:
        raise ValueError("the judge has no usable correction (sensitivity + specificity = 1)")

    rng = np.random.default_rng(seed) if seed is not None else np.random.default_rng()
    estimates = []
    for _ in range(bootstrap_iterations):
        s_draw = sample[rng.integers(0, sample.size, sample.size)]
        idx = rng.integers(0, labels.size, labels.size)
        r = rates(labels[idx], preds[idx])
        if r is None:
            continue
        value = rogan_gladen(float(s_draw.mean()), *r)
        if value is None:
            continue
        estimates.append(min(1.0, max(0.0, value)))
    if not estimates:
        raise ValueError("no bootstrap replicate produced a valid correction")
    alpha = (1 - confidence) / 2
    low, high = np.percentile(estimates, [100 * alpha, 100 * (1 - alpha)])
    return {
        "raw": round(raw, 4),
        "corrected": round(min(1.0, max(0.0, point)), 4),
        "ci_low": round(float(low), 4),
        "ci_high": round(float(high), 4),
        "confidence": confidence,
        "failure_sensitivity": round(sens, 4),
        "pass_specificity": round(spec, 4),
        "n_sample": int(sample.size),
    }
