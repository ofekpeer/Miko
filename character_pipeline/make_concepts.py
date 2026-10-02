"""Step 1: concept images for Miko's new body (free FLUX.1-schnell on Hugging Face).

The face is left blank on purpose: eyes, eyelids and mouth are built as
separate animated 3D parts in Blender, so the 3D generator must not bake a
painted face into the texture.

    python make_concepts.py 11 22 33
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from gradio_client import Client

OUT = Path(__file__).resolve().parent / "concepts"

PROMPT = (
    "A single cute small round creature character, full body, front view, Pixar animation film style "
    "3D render. Plump pear-shaped body with a big round head, short stubby arms held slightly away from "
    "the body, two small round feet, two tall rounded fennec-fox ears with softly glowing cyan tips, "
    "a small fluffy tail. Soft short velvety apricot-orange fur, cream white belly. Completely blank "
    "smooth face with no eyes, no mouth and no nose. Standing upright, symmetrical, centered, isolated "
    "on a plain white background, soft even studio lighting."
)


def generate(client: Client, seed: int) -> Path:
    result, _ = client.predict(
        prompt=PROMPT, seed=seed, randomize_seed=False, width=1024, height=1024,
        num_inference_steps=4, api_name="/infer",
    )
    path = result["path"] if isinstance(result, dict) else result
    target = OUT / f"concept_seed{seed}{Path(path).suffix or '.webp'}"
    shutil.copy(path, target)
    return target


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    client = Client("black-forest-labs/FLUX.1-schnell", verbose=False)
    for seed in [int(s) for s in sys.argv[1:]] or [11, 22, 33]:
        print("saved", generate(client, seed), flush=True)
