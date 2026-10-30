"""Small helpers shared by the fit sections."""
import statistics

from knowledge.values import as_int  # noqa: F401 - the fit sections' int reader (int(v), None when not a number)


def median_of(v):
    """The median of the values, None when there are none."""
    return statistics.median(v) if v else None


def rounded(v, n=3):
    """round(v, n), with None passed through."""
    return None if v is None else round(v, n)
