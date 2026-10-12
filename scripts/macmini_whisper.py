"""Bridge to the existing Mac shared Whisper engine; no network/model download."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


def transcribe_local(audio, language, engine, work):
    # The parent queue worker bounds the entire process group to 30 min / 6 GiB.
    output = work / "local-inference.json"
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    env.update(HOME=str(work / "home"), HF_HUB_CACHE=engine["model_cache"],
               HF_HUB_OFFLINE="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
               HF_HUB_DISABLE_TELEMETRY="1", WHISPER_DEVICE="mlx", OMP_NUM_THREADS="4")
    if engine.get("user_site"):
        env["PYTHONPATH"] = engine["user_site"]
    subprocess.run([engine["python"], str(Path(__file__).resolve()), "--infer",
                    engine["transcriber"], str(audio), language, str(output)],
                   env=env, check=True, timeout=1800)
    result = json.loads(output.read_text())
    if not result.get("text", "").strip():
        raise ValueError("Local Whisper returned no transcript")
    return result["text"], [], {key: result[key] for key in
                               ("backend_label", "transcribe_time_sec", "language")}


def infer():
    _, _, helper, audio, language, output = sys.argv
    spec = importlib.util.spec_from_file_location("shared_podcast_transcriber", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.transcribe_audio(audio, model_name="medium", language=language)
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    infer()
