"""Evaluation of real predictions only; no inference or mock selection here."""
from collections import defaultdict
import math
from statistics import mean

from .labels import parse_timecode, parse_verdict


def scores(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def match_events(truth, predicted, tolerance_sec, same_type=False):
    """Maximum-cardinality one-to-one matching; nearest edges tried first.

    Optimizes match count, not global minimum timestamp error. Unknown events
    participate in localization, and never in type classification.
    """
    adjacency = {}
    for i, expected in enumerate(truth):
        adjacency[i] = sorted((j for j, actual in enumerate(predicted)
            if abs(expected["timecode_sec"] - actual["timecode_sec"]) <= tolerance_sec
            and (not same_type or expected["violation_type"] == actual["violation_type"])),
            key=lambda j: abs(expected["timecode_sec"] - predicted[j]["timecode_sec"]))
    matched = {}

    def assign(i, seen):
        for j in adjacency[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in matched or assign(matched[j], seen):
                matched[j] = i
                return True
        return False

    for i in range(len(truth)):
        assign(i, set())
    return [(i, j) for j, i in matched.items()]


def compute_metrics(labels, predictions, tolerance_sec=3.):
    """predictions maps evaluated video filename to list of Finding-like dictionaries.

    Missing keys are unevaluated, never silently interpreted as negative results.
    Classification uses only completely typed positive videos and negative videos.
    This avoids counting predictions on unclassified events as false positives.
    """
    if not math.isfinite(tolerance_sec) or tolerance_sec < 0:
        raise ValueError("Localization tolerance must be finite and non-negative")
    grouped = defaultdict(list)
    for row in labels:
        grouped[row["video"]].append(row)
    video_tp = video_fp = video_fn = video_tn = 0
    loc_tp = loc_fp = loc_fn = 0
    errors, nearest_errors, per_type, review = [], [], defaultdict(lambda: [0, 0, 0]), []
    matched_5 = type_correct = type_localized = 0
    confusion = defaultdict(lambda: defaultdict(int))
    class_videos = 0
    localization_missing_time = 0
    for video, actual in predictions.items():
        expected = grouped[video]
        positive = [row for row in expected if parse_verdict(row["has_violation"])]
        truth_yes, predicted_yes = bool(positive), bool(actual)
        video_tp += truth_yes and predicted_yes
        video_fp += not truth_yes and predicted_yes
        video_fn += truth_yes and not predicted_yes
        video_tn += not truth_yes and not predicted_yes
        reasons = []
        if truth_yes != predicted_yes:
            reasons.append("FN" if truth_yes else "FP")
        timed = []
        for row in positive:
            stamp = parse_timecode(row["timecode_sec"])
            if stamp is None:
                localization_missing_time += 1
            else:
                timed.append({**row, "timecode_sec": stamp})
        pairs = match_events(timed, actual, tolerance_sec)
        matched_5 += len(match_events(timed, actual, 5.))
        nearest_errors.extend(abs(timed[i]["timecode_sec"] - actual[j]["timecode_sec"])
                              for i, j in match_events(timed, actual, float("inf")))
        loc_tp += len(pairs)
        loc_fn += len(timed) - len(pairs)
        # Incompletely timed positives cannot reliably penalize unmatched predictions.
        if len(timed) == len(positive):
            loc_fp += len(actual) - len(pairs)
        errors.extend(abs(timed[i]["timecode_sec"] - actual[j]["timecode_sec"]) for i, j in pairs)
        if len(pairs) < len(timed) or len(actual) > len(pairs):
            reasons.append("localization")
        fully_typed = (not any(row.get("review_status") in {"needs_review", "source_observed"} for row in expected)
            and all(row.get("violation_type") not in (None, "", "unknown") for row in positive))
        if fully_typed and len(timed) == len(positive):
            class_videos += 1
            matches = match_events(timed, actual, tolerance_sec, same_type=True)
            localized = match_events(timed, actual, tolerance_sec)
            localized_truth, localized_actual = {i for i, _ in localized}, {j for _, j in localized}
            for i, j in localized:
                wanted, got = timed[i]["violation_type"], actual[j]["violation_type"]
                confusion[wanted][got] += 1
                type_correct += wanted == got
                type_localized += 1
            for i, row in enumerate(timed):
                if i not in localized_truth:
                    confusion[row["violation_type"]]["__missed__"] += 1
            for j, row in enumerate(actual):
                if j not in localized_actual:
                    confusion["__spurious__"][row["violation_type"]] += 1
            matched_truth, matched_actual = {i for i, _ in matches}, {j for _, j in matches}
            for i, row in enumerate(timed):
                per_type[row["violation_type"]][0 if i in matched_truth else 2] += 1
            for j, row in enumerate(actual):
                if j not in matched_actual:
                    per_type[row["violation_type"]][1] += 1
            if len(matches) < max(len(timed), len(actual)):
                reasons.append("classification")
        if reasons:
            review.append({"video": video, "reasons": reasons})
    evaluated = len(predictions)
    totals = [sum(values[i] for values in per_type.values()) for i in range(3)]
    return {"evaluated_videos": evaluated, "unevaluated_videos": sorted(set(grouped) - set(predictions)),
        "video_detection": {**scores(video_tp, video_fp, video_fn), "tn": video_tn,
            "accuracy": (video_tp + video_tn) / evaluated,
            "reference": "original_organizer_verdicts_not_legal_proof"} if evaluated else None,
        "localization": {**scores(loc_tp, loc_fp, loc_fn), "tolerance_sec": tolerance_sec,
            "mean_abs_time_error_sec_matched": mean(errors) if errors else None,
            "max_abs_time_error_sec_matched": max(errors) if errors else None,
            "events_within_3_sec": loc_tp if tolerance_sec == 3 else None,
            "events_within_5_sec": matched_5,
            "mean_abs_time_error_sec_nearest_match": mean(nearest_errors) if nearest_errors else None,
            "nearest_matched_events": len(nearest_errors),
            "matched_events": len(errors), "truth_events_missing_time": localization_missing_time} if evaluated else None,
        "classification": {"evaluated_videos": class_videos, "micro": scores(*totals),
            "per_type": {key: scores(*values) for key, values in per_type.items()},
            "type_accuracy_on_localized_events": type_correct / type_localized if type_localized else None,
            "localized_events": type_localized, "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
            "reference_status": "provisional_source_observations" if any(
                r.get("review_status") == "source_observed" for r in labels) else "manual_types",
            "unknown_policy": "Exclude entire unknown/needs_review videos from classification"} if class_videos else None,
        "annotation_coverage": {"total_videos": len(grouped),
            "needs_review_videos": sorted({r["video"] for r in labels if r.get("review_status") == "needs_review"}),
            "typed_positive_events": sum(parse_verdict(r["has_violation"])
                and r.get("violation_type") not in (None, "", "unknown")
                and r.get("review_status") not in {"needs_review", "source_observed"} for r in labels),
            "unknown_positive_events": sum(parse_verdict(r["has_violation"])
                and r.get("violation_type") in (None, "", "unknown") for r in labels)},
        "manual_review": review}
