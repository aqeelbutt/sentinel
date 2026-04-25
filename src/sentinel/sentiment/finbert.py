"""FinBERT backend (ProsusAI/finbert).

Opt-in: requires the `finbert` extra (`uv sync --extra finbert`), which
pulls in torch + transformers. Model weights (~500 MB) download on first
use via Hugging Face Hub and are cached in ~/.cache/huggingface.

Why FinBERT: trained on financial news headlines; outputs per-class
probabilities over {positive, negative, neutral}. We collapse to a signed
score = p_pos - p_neg in [-1, 1], which matches our contract. Generic
sentiment libraries (VADER, TextBlob) misread financial jargon ("beat
estimates" → positive; "guidance cut" → negative; "short interest rose"
is bearish for the stock) and are not acceptable substitutes.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)


class FinBERTBackend:
    name = "finbert-prosusai-v1"
    max_batch = 16

    def __init__(self, model_name: str = "ProsusAI/finbert") -> None:
        self._model_name = model_name
        self._pipeline = None  # lazy

    def _ensure_loaded(self) -> None:
        if self._pipeline is not None:
            return
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
        except ImportError as e:
            raise RuntimeError(
                "FinBERT backend requires the 'finbert' extra. "
                "Install with: uv sync --extra finbert   (or: pip install 'sentinel[finbert]')"
            ) from e
        log.info("loading FinBERT weights — first run may download ~500 MB")
        tok = AutoTokenizer.from_pretrained(self._model_name)
        mdl = AutoModelForSequenceClassification.from_pretrained(self._model_name)
        self._pipeline = pipeline(
            "sentiment-analysis",
            model=mdl,
            tokenizer=tok,
            top_k=None,
            truncation=True,
            max_length=256,
        )

    def score(self, text: str) -> float:
        return self.score_batch([text])[0]

    def score_batch(self, texts: list[str]) -> list[float]:
        if not texts:
            return []
        self._ensure_loaded()
        assert self._pipeline is not None
        results = self._pipeline(texts, batch_size=min(self.max_batch, len(texts)))
        out: list[float] = []
        for r in results:
            # transformers returns list-of-list when top_k=None; otherwise dict.
            items = r if isinstance(r, list) else [r]
            p_pos = 0.0
            p_neg = 0.0
            for item in items:
                label = item["label"].lower()
                score = float(item["score"])
                if label.startswith("pos"):
                    p_pos = score
                elif label.startswith("neg"):
                    p_neg = score
            out.append(p_pos - p_neg)
        return out
