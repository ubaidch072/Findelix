# ml/summarizer.py
import os
from typing import Optional, Tuple, List
from transformers import pipeline

def _word_trim(text: str, bounds: Optional[Tuple[int, int]]) -> str:
    if not text or not bounds:
        return (text or "").strip()
    wmin, wmax = bounds
    words = text.split()
    if len(words) > wmax:
        text = " ".join(words[:wmax]).rstrip()
        if not text.endswith((".", "!", "?")):
            text += "."
        return text
    # gently pad short outputs by keeping as-is (generator usually hits >=100 words)
    return text.strip()

def _chunk(text: str, max_chars: int = 4000) -> List[str]:
    """Simple char-based chunking with soft boundaries; summarizer will condense."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return [t]
    chunks = []
    start = 0
    while start < len(t):
        end = min(len(t), start + max_chars)
        # try to cut at a sentence boundary near the end
        cut = t[start:end]
        last_dot = cut.rfind(".")
        if last_dot >= int(0.7 * len(cut)):
            end = start + last_dot + 1
        chunks.append(t[start:end].strip())
        start = end
    return [c for c in chunks if c]

class Summarizer:
    """
    Local summarizer used ONLY for the summary field.
    Respects MODEL_CKPT from env (HF model id or local folder).
    """
    def __init__(self):
        ckpt = os.getenv("MODEL_CKPT", "mrm8488/t5-small-finetuned-summarize-news").strip()
        # CPU-friendly defaults
        self.summarizer = pipeline(
            "summarization",
            model=ckpt,
            tokenizer=ckpt,
            device=-1,             # CPU
            framework="pt",
        )

    def summarize_100_150_words(self, text: str, target_min: int = 100, target_max: int = 150) -> str:
        # 1) chunk long text
        parts = _chunk(text, max_chars=3500)

        # 2) summarize each part, then summarize the concatenation to hit the target
        partials: List[str] = []
        for p in parts:
            # token lengths are model-dependent; use generous bounds, then word-trim
            out = self.summarizer(
                p,
                max_length=260,   # ~ 170–190 words for T5 small-ish; we'll trim later
                min_length=60,
                do_sample=False,
            )[0]["summary_text"].strip()
            partials.append(out)

        merged = " ".join(partials).strip()
        # one more pass to compress to target range
        final = self.summarizer(
            merged,
            max_length=320,  # upper bound; we trim to words anyway
            min_length=80,
            do_sample=False,
        )[0]["summary_text"].strip()

        return _word_trim(final, (target_min, target_max))
