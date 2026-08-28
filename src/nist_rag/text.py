import hashlib
import math
import re
from collections import Counter
from typing import Dict, Iterable, List


WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*")


def tokenize(text: str) -> List[str]:
    return [word.lower() for word in WORD_RE.findall(text)]


def normalize_whitespace(text: str) -> str:
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def hashed_vector(tokens: Iterable[str], dimensions: int = 512) -> Dict[int, float]:
    """Dependency-free feature hashing used as the offline semantic fallback."""
    counts: Counter[int] = Counter()
    values = list(tokens)
    for token in values:
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        counts[int.from_bytes(digest, "big") % dimensions] += 1
    for left, right in zip(values, values[1:]):
        digest = hashlib.blake2b((left + " " + right).encode(), digest_size=8).digest()
        counts[int.from_bytes(digest, "big") % dimensions] += 0.5
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    return {key: value / norm for key, value in counts.items()}


def cosine(left: Dict[int, float], right: Dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())

