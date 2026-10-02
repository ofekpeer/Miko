"""Step 2: concept image -> 3D mesh with free Hugging Face Spaces.

    python make_3d.py shape   concepts/concept_seed11.png   # Hunyuan3D-2 geometry only (40 s GPU)
    python make_3d.py trellis concepts/concept_seed11.png   # TRELLIS.2 textured mesh (2 x 120 s GPU)

Free ZeroGPU quota per day: 120 s without an account, 300 s with a free
account. A token saved with `huggingface_hub.login()` is used automatically.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import httpx
import ipv4_only  # noqa: F401  (this network's IPv6 and some HF addresses stall)
from gradio_client import Client, handle_file

OUT = Path(__file__).resolve().parent / "models"


def _token() -> str | None:
    try:
        from huggingface_hub import get_token
        return get_token()
    except Exception:
        return None


def _client(space: str) -> Client:
    for attempt in range(4):
        try:
            # Connections from this network can take ~13 s to open; allow for it.
            return Client(space, verbose=False, token=_token(),
                          httpx_kwargs={"timeout": httpx.Timeout(600.0, connect=45.0)})
        except Exception as error:  # flaky connects on this network
            print(f"connect attempt {attempt + 1} to {space} failed: {type(error).__name__}")
    raise SystemExit(f"could not reach {space}")


def _file(value) -> str:
    """Spaces return a path, a FileData dict, or a gr.update dict wrapping one."""
    if isinstance(value, dict):
        value = value.get("value", value)
        if isinstance(value, dict):
            value = value.get("path") or value.get("url")
    return str(value)


def shape(image: Path) -> Path:
    client = _client("tencent/Hunyuan3D-2")
    result = client.predict(
        caption=None, image=handle_file(str(image)),
        mv_image_front=None, mv_image_back=None, mv_image_left=None, mv_image_right=None,
        steps=30, guidance_scale=5.0, seed=1234, octree_resolution=256,
        check_box_rembg=True, num_chunks=8000, randomize_seed=False,
        api_name="/shape_generation",
    )
    target = OUT / f"{image.stem}_hunyuan_shape.glb"
    shutil.copy(_file(result[0]), target)
    if len(result) > 2:
        (OUT / f"{image.stem}_hunyuan_stats.json").write_text(json.dumps(result[2], indent=2), encoding="utf-8")
    return target


def trellis(image: Path) -> Path:
    client = _client("microsoft/TRELLIS.2")
    client.predict(api_name="/start_session")
    prepared = client.predict(input=handle_file(str(image)), api_name="/preprocess_image")
    client.predict(image=handle_file(prepared["path"] if isinstance(prepared, dict) else prepared),
                   seed=1234, resolution="1024", api_name="/image_to_3d")
    glb, _download = client.predict(decimation_target=200000, texture_size=2048, api_name="/extract_glb")
    target = OUT / f"{image.stem}_trellis2.glb"
    shutil.copy(_file(glb), target)
    return target


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    mode, source = sys.argv[1], Path(sys.argv[2]).resolve()
    print("saved", {"shape": shape, "trellis": trellis}[mode](source))
