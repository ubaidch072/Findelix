# ml/summarizer.py
from __future__ import annotations
from typing import Optional
import re

from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

# Swap this with your fine-tuned checkpoint path when ready
DEFAULT_CKPT = "mrm8488/t5-small-finetuned-summarize-news"

class Summarizer:
    def __init__(self, model_ckpt: str = DEFAULT_CKPT, device: Optional[str] = None):
        self.tokenizer = AutoTokenizer.from_pretrained(model_ckpt)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_ckpt)
        try:
            import torch
            if device is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
            self.device = device
            self.model.to(device)
        except Exception:
            self.device = "cpu"

    def _word_count(self, text: str) -> int:
        return len(re.findall(r"\w+", text or ""))

    def summarize_100_150_words(self, text: str, *, target_min=100, target_max=150) -> str:
        if not text or self._word_count(text) < 20:
            return "No substantial coverage found. The company shows limited recent activity online."

        approx_min_tokens = int(target_min * 1.4)
        approx_max_tokens = int(target_max * 1.5)

        inputs = self.tokenizer([text], max_length=2048, truncation=True, return_tensors="pt")
        try:
            import torch
            if self.device != "cpu":
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
        except Exception:
            pass

        summary_ids = self.model.generate(
            **inputs,
            num_beams=4,
            length_penalty=0.9,
            min_length=approx_min_tokens,
            max_length=approx_max_tokens,
            no_repeat_ngram_size=3,
            early_stopping=True,
        )
        summary = self.tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()

        words = summary.split()
        if len(words) > target_max:
            summary = " ".join(words[:target_max]).rstrip()
            if not summary.endswith((".", "!", "?")):
                summary += "."

        # If too short, one more pass nudging min up
        if self._word_count(summary) < target_min:
            summary_ids = self.model.generate(
                **inputs,
                num_beams=4,
                length_penalty=0.9,
                min_length=int((target_min + 15) * 1.45),
                max_length=approx_max_tokens,
                no_repeat_ngram_size=3,
                early_stopping=True,
            )
            summary = self.tokenizer.decode(summary_ids[0], skip_special_tokens=True).strip()
            words = summary.split()
            if len(words) > target_max:
                summary = " ".join(words[:target_max]).rstrip()
                if not summary.endswith((".", "!", "?")):
                    summary += "."

        return summary
