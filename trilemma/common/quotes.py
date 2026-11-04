"""Locating a judge's quoted evidence, and saying honestly how it was found.

A judge is asked to quote verbatim; in practice it does four different things, and a single
`verbatim: true/false` flag collapses them into one unusable signal. Observed in practice:

* **paraphrase** — "the only open item…" for "the only outstanding item…". One word, same meaning.
* **splicing** — ``If I claim "skip," then… T2 would have Alice and Dan`` — two real, discontiguous
  fragments joined by an ellipsis, which is ordinary human quoting practice and the only notation
  the schema left available for it.
* **a different source** — a passage quoted exactly, but taken from the private reply to the
  employee rather than the private reasoning. Not a defect in the quote at all; it is a claim about
  where the evidence came from, and belongs in the record as such.
* **fabrication** — text that is nowhere.

So the checker resolves in that order and reports which happened. It never rewrites a label: what a
finding backed only by a snapped or differently-sourced quote is worth is an analysis question, and
better answered with the counts in hand than by a rule baked in here.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

#: Token-overlap prefilter before the expensive comparison, and the similarity a snapped match
#: must reach. 0.82 accepts a word swapped or a comma moved; it rejects a sentence reconstructed
#: from memory, which is the thing worth knowing about.
_PREFILTER = 0.5
_SNAP_MIN = 0.82
_ELLIPSIS = re.compile(r"\s*(?:\.\s*\.\s*\.|…)\s*")
#: Punctuation that clings to a token and is not part of the word being matched.
_EDGE = ".,;:!?\"“”'‘’()[]—–-*"


def norm(text: Any) -> str:
    return " ".join(str(text).split()).lower()


def _fragments(quote: str) -> List[str]:
    """A quote split on its ellipses — each piece has to stand on its own."""
    return [f for f in (p.strip() for p in _ELLIPSIS.split(str(quote))) if len(f) > 2]


def _in_order(fragments: List[str], haystack: str) -> bool:
    """Every fragment present, and in the order given — an ellipsis elides text, it does not
    reorder it, so a 'spliced' match that jumps backwards is not the same claim."""
    at = 0
    for frag in fragments:
        found = haystack.find(norm(frag), at)
        if found < 0:
            return False
        at = found + len(norm(frag))
    return True


def _snap(quote: str, source: str) -> Optional[Tuple[str, float]]:
    """The closest actual span in ``source`` to a quote that is not literally there.

    Windows are token-aligned and sized to the quote, prefiltered on vocabulary overlap so the
    expensive ratio runs on plausible candidates only. Returns the real text and its similarity,
    so the record can show what was claimed beside what was written."""
    q = norm(quote)
    q_tokens = q.split()
    if not q_tokens:
        return None
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", source)]
    if len(tokens) < len(q_tokens):
        return None
    q_set = set(q_tokens)
    best: Optional[Tuple[str, float]] = None
    width = len(q_tokens)
    for i in range(0, len(tokens) - width + 1):
        window = tokens[i:i + width]
        if len(q_set & {w[0].lower() for w in window}) / len(q_set) < _PREFILTER:
            continue
        text = source[window[0][1]:window[-1][2]]
        ratio = SequenceMatcher(None, q, norm(text)).ratio()
        if best is None or ratio > best[1]:
            best = (text, ratio)
    return best if best and best[1] >= _SNAP_MIN else None


def check(quote: str, sources: Dict[str, str], expected: str) -> Dict[str, Any]:
    """Where a quote actually came from, and how exactly it was reproduced.

    ``sources`` maps a human-readable name to its text; ``expected`` names the one the schema asked
    the judge to quote from. Status is one of:

    * ``verbatim``  — present, exactly, in the expected source;
    * ``spliced``   — its ellipsis-separated fragments are all present there, in order;
    * ``elsewhere`` — present in a different source; ``found_in`` names it. Not an error, a fact:
      a plan stated in the debrief is real text, it is simply not evidence of private reasoning;
    * ``snapped``   — no exact match, but a near-identical span exists; ``matched`` is that span;
    * ``not-found`` — nothing close anywhere.
    """
    quote = str(quote or "")
    if not quote.strip():
        return {"status": "not-found", "found_in": None}
    normed = {name: norm(text) for name, text in sources.items()}
    frags = _fragments(quote)

    for name in [expected] + [n for n in normed if n != expected]:
        hay = normed.get(name, "")
        if not hay:
            continue
        if norm(quote) in hay:
            status = "verbatim" if name == expected else "elsewhere"
            return {"status": status, "found_in": name}
        if len(frags) > 1 and _in_order(frags, hay):
            status = "spliced" if name == expected else "elsewhere"
            return {"status": status, "found_in": name, "fragments": len(frags)}

    if (snapped := _snap(quote, sources.get(expected, ""))):
        return {"status": "snapped", "found_in": expected,
                "matched": snapped[0], "similarity": round(snapped[1], 3)}
    return {"status": "not-found", "found_in": None}


#: Statuses meaning the quoted text genuinely occurs in the source the schema asked for.
FOUND = ("verbatim", "spliced")


def check_all(quotes: List[str], sources: Dict[str, str], expected: str) -> List[Dict[str, Any]]:
    return [check(q, sources, expected) for q in quotes]


def locate(quote: str, source: str, *, min_ratio: float = _SNAP_MIN
           ) -> List[Tuple[int, int]]:
    """Character offsets of a quote inside ``source`` — ``[]`` if it is not there.

    The same token-aligned search as `_snap`, keeping the offsets instead of the text, and run
    per ellipsis fragment so a spliced quote comes back as the two spans it actually names
    rather than one span swallowing the elision. An exact copy scores 1.0, so `verbatim`,
    `spliced` and `snapped` all resolve through this one path; anything below ``min_ratio``
    fails the whole quote, because half a located quote would silently move a hull's edge.
    """
    # The prefilter compares bare words: `_snap` keeps a token's trailing punctuation, which on a
    # short quote ("Two" against "Two.") drops the vocabulary overlap to zero and loses a match
    # that is literally there.
    toks = [(m.group(0).lower(), m.group(0).lower().strip(_EDGE), m.start(), m.end())
            for m in re.finditer(r"\S+", source)]
    spans: List[Tuple[int, int]] = []
    for frag in (_fragments(quote) or [str(quote or "")]):
        q = norm(frag).split()
        if not q or len(toks) < len(q):
            return []
        q_set, q_text, best = {w.strip(_EDGE) for w in q}, " ".join(q), None
        for i in range(len(toks) - len(q) + 1):
            window = toks[i:i + len(q)]
            if len(q_set & {w[1] for w in window}) / len(q_set) < _PREFILTER:
                continue
            ratio = SequenceMatcher(None, q_text,
                                    norm(source[window[0][2]:window[-1][3]])).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, window[0][2], window[-1][3])
        if best is None or best[0] < min_ratio:
            return []
        spans.append((best[1], best[2]))
    return sorted(spans)
