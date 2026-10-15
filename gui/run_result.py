"""Validate counts reported by this process; never read a prior run's summary."""
import json
PREFIX = "PAPERPULL_RUN_RESULT "
FIELDS = ("manual_review", "failed", "validation_failures", "new_files")
# Counts an app from before they existed does not send. Absent is zero,
# present has to be a count like the rest, so an install the panel has
# not refreshed yet still reports its run.
OPTIONAL = ("wrong_document", "stopped", "name_review", "alike")
# Counts the panel says but that never make a run one to look at.
# Documents that share a type and a date with another are listed again on
# every run and can never be saved, even once taken by hand from the
# provider's own page, so a run that counted them would need a look for
# good, and the row would never again say it is due or overdue.
SAID_ONLY = ("alike",)


def _count(value):
    return type(value) is int and value >= 0


def parse(line):
    if not line.startswith(PREFIX):
        return None
    try:
        data = json.loads(line[len(PREFIX):])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not all(_count(data.get(k)) for k in FIELDS):
        return None
    if any(k in data and not _count(data[k]) for k in OPTIONAL):
        return None
    result = {k: data[k] for k in FIELDS}
    result.update({k: data.get(k, 0) for k in OPTIONAL})
    # Part of manual_review, never more than it.
    if result["name_review"] > result["manual_review"]:
        return None
    result["attention"] = any(result[k] for k in FIELDS + OPTIONAL
                              if k != "new_files" and k not in SAID_ONLY)
    return result
