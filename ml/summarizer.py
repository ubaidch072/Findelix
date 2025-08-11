# ml/summarizer.py
from __future__ import annotations
from typing import Optional
import os, re

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

# Small, fast default model; override in env if you fine-tune your own
# e.g., MODEL_CKPT=/models/model (when baking into a Docker image)
DEFAULT_CKPT = os.getenv("MODEL_CKPT", "mrm8488/t5-small-finetuned-summarize-news")

# Cap source length for tiny models (T5-small max is 512; 768 is usually safe after truncation)
MAX_SOURCE_TOKENS = int(os.getenv("SUMMARIZER_MAX_SOURCE_TOKENS", "768"))

class Summarizer:
    """
    Lightweight seq2seq summarizer designed for CPU-only hosting (e.g., Render).
    Produces ~100–150 word summaries, trimmed at the end if needed.
    """

    def __init__(self, model_ckpt: str = DEFAULT_CKPT, device: Optional[str] = None):
        self.tokenizer = AutoTokenizer.from_pretrained(model_ckpt)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_ckpt)

        # Force CPU by default (safe on low-RAM instances); allow override
        self.device = device or "cpu"
        try:
            import torch  # noqa
            self.model.to(self.device)
        except Exception:
            # If torch isn't available or device move fails, keep CPU
            self.device = "cpu"

    # ---------------- internal helpers ----------------

    def _word_count(self, text: str) -> int:
        return len(re.findall(r"\w+", text or ""))

    def _trim_to_max_words(self, text: str, max_words: int) -> str:
        if not text:
            return text
        words = text.split()
        if len(words) <= max_words:
            return text
        text = " ".join(words[:max_words]).rstrip()
        if not text.endswith((".", "!", "?")):
            text += "."
        return text

    # ---------------- public API ----------------

    def summarize_100_150_words(self, text: str, *, target_min=100, target_max=150) -> str:
        """
        Generate a ~100–150 word summary.
        - Uses T5-style "summarize:" prefix (works better on small checkpoints)
        - Truncates input to keep generation fast on CPU
        - Enforces upper bound and nudges up if too short
        """
        if not text or self._word_count(text) < 20:
            return "No substantial coverage found. The company shows limited recent activity online."

        # T5-family models benefit from this prefix
        prompt = "summarize: " + text

        # Tokenize/truncate the source
        inputs = self.tokenizer(
            [prompt],
            max_length=MAX_SOURCE_TOKENS,
            truncation=True,
            return_tensors="pt",
        )

        try:
            # If we’re actually on CPU, this is a no-op
            if self.device != "cpu":
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
        except Exception:
            pass

        # Token budget heuristics (tokens ≈ 1.4–1.5 × words for seq2seq)
        approx_min_tokens = int(target_min * 1.4)
        approx_max_tokens = int(target_max * 1.5)

        summary_ids = self.model.generate(
            **inputs,
            num_beams=4,              # good quality on CPU
            length_penalty=0.9,
            min_length=approx_min_tokens,
            max_length=approx_max_tokens,
            no_repeat_ngram_size=3,
            early_stopping=True,
        )
        summary = self.tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()

        # Enforce 100–150 words (upper bound first)
        summary = self._trim_to_max_words(summary, target_max)

        # If still short, lightly nudge: add one more constrained pass
        if self._word_count(summary) < target_min:
            summary_ids = self.model.generate(
                **inputs,
                num_beams=4,
                length_penalty=0.9,
                min_length=int((target_min + 10) * 1.45),
                max_length=approx_max_tokens,
                no_repeat_ngram_size=3,
                early_stopping=True,
            )
            summary = self.tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()
            summary = self._trim_to_max_words(summary, target_max)

        return summary
