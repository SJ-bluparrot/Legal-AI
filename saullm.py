"""
SaulLM model reference and generation utilities.
Shared between app.py and complaint_drafter.py.
Call set_model_refs(model, tokenizer) at server startup before any generate() call.
"""
import gc
import logging
import torch

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None


def set_model_refs(model, tokenizer):
    global _model, _tokenizer
    _model = model
    _tokenizer = tokenizer


def is_loaded() -> bool:
    return _model is not None and _tokenizer is not None


def generate(prompt: str, max_new_tokens: int = 500, greedy: bool = False) -> str:
    """
    Generate text using SaulLM.
    greedy=True for deterministic output (complaint drafting).
    greedy=False with temperature=0.7 for varied output (was used for Q&A — now unused).
    Raises RuntimeError if model not loaded.
    """
    if _model is None or _tokenizer is None:
        raise RuntimeError("SaulLM model not loaded. Call set_model_refs() first.")

    try:
        inputs = _tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        inputs = {k: v.cuda(non_blocking=True) for k, v in inputs.items()}

        input_len = inputs["input_ids"].shape[1]
        max_tokens = min(max_new_tokens, 2048 - input_len)

        gen_kwargs = dict(
            max_new_tokens=max_tokens,
            repetition_penalty=1.1,
            pad_token_id=_tokenizer.eos_token_id,
            eos_token_id=_tokenizer.eos_token_id,
        )
        if greedy:
            gen_kwargs["do_sample"] = False
        else:
            gen_kwargs["do_sample"] = True
            gen_kwargs["temperature"] = 0.7
            gen_kwargs["top_p"] = 0.95

        with torch.no_grad():
            output_ids = _model.generate(**inputs, **gen_kwargs)

        new_tokens = output_ids[0][input_len:]
        result = _tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

        for artifact in ("<SYS>", "<<SYS>>", "<</SYS>>", "[INST]", "[/INST]", "<s>"):
            if result.startswith(artifact):
                result = result[len(artifact):].strip()

        if result and len(new_tokens) >= max_tokens:
            logger.warning(f"SaulLM response may be truncated — hit token limit ({max_tokens})")

        del output_ids, inputs
        gc.collect()
        torch.cuda.empty_cache()

        return result

    except Exception as e:
        logger.error(f"SaulLM generation error: {e}")
        gc.collect()
        torch.cuda.empty_cache()
        raise
