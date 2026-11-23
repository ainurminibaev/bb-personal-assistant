#!/usr/bin/env python3
"""One-off model build for bb-speech-ru: GigaAM and VoxLingua107 → ONNX, so the runtime needs only
numpy + onnxruntime + sentencepiece (no torch: −300 МБ RAM). Runs in a temporary build venv
(torch, gigaam, speechbrain, onnx, onnxscript) — deploy/speech/install.sh creates and deletes it.

  export_models.py gigaam <out_dir>        GigaAM v3_e2e_ctc (Sber, MIT; GIGAAM_SOURCE = checked local copy): fp32 → ONNX
  export_models.py gigaam-quantize <out_dir>   (second step, separate process: export peak + quantize peak
                                               together need ~2.5 GB more)
  export_models.py lid <out_dir>           speechbrain/lang-id-voxlingua107-ecapa (Apache-2.0): wav → log-probs[107]
                                           (Fbank + sentence mean norm + ECAPA + classifier inside the graph)
"""
import os
import shutil
import sys

import torch

GIGAAM = os.environ.get("GIGAAM_MODEL", "v3_e2e_ctc")


def gigaam(out: str) -> None:
    """GigaAM from a local copy of the official Hugging Face repository (ai-sage/GigaAM-v3, branch e2e_ctc),
    downloaded and checked by fetch.py: config.json, modeling_gigaam.py, pytorch_model.bin, tokenizer.model."""
    import importlib.util
    src = os.environ["GIGAAM_SOURCE"]
    # The checked modeling file is imported directly: AutoModel(trust_remote_code) would demand every package the
    # file imports anywhere (pyannote — only for long-form VAD, not used here). Its config refers to the module
    # by the name "modeling_gigaam", so it is registered under that name.
    spec = importlib.util.spec_from_file_location("modeling_gigaam", os.path.join(src, "modeling_gigaam.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["modeling_gigaam"] = mod
    spec.loader.exec_module(mod)
    # Built directly, weights loaded by hand: from_pretrained in transformers 5 builds on the "meta" device, which
    # the GigaAM feature extractor (real CPU tensors in __init__) does not support.
    cfg = mod.GigaAMConfig.from_pretrained(src)
    cfg._name_or_path = src                         # the model resolves tokenizer.model from here
    m = mod.GigaAMModel(cfg)
    sd = torch.load(os.path.join(src, "pytorch_model.bin"), map_location="cpu", weights_only=True)
    missing, unexpected = m.load_state_dict(sd, strict=False)
    if missing or unexpected:
        sys.exit(f"GigaAM weights do not match the model: missing {missing[:5]}, unexpected {unexpected[:5]}")
    m.eval()
    m.to_onnx(out)                                  # -> <out>/v3_e2e_ctc.onnx (+ its yaml config)
    shutil.copy(os.path.join(src, "tokenizer.model"), os.path.join(out, "tokenizer.model"))
    print("exported", sorted(os.listdir(out)))


def gigaam_quantize(out: str) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic
    src = os.path.join(out, f"{GIGAAM}.onnx")
    quantize_dynamic(src, os.path.join(out, "gigaam.int8.onnx"), weight_type=QuantType.QUInt8)
    for f in os.listdir(out):                        # fp32 graph and its config are not needed at run time
        if f.startswith(GIGAAM) and f.endswith((".onnx", ".yaml", ".data")):
            os.unlink(os.path.join(out, f))
    print("quantized", sorted(os.listdir(out)))


def lid(out: str) -> None:
    from speechbrain.inference.classifiers import EncoderClassifier
    sb = EncoderClassifier.from_hparams(source=os.environ.get("LID_SOURCE", "speechbrain/lang-id-voxlingua107-ecapa"),
                                        savedir=os.path.join(out, "sb"))

    class LID(torch.nn.Module):
        """Fbank как в speechbrain, но STFT — свёрткой с матрицей ДПФ: комплексный torch.stft в ONNX не выгружается."""

        def __init__(self, mods, n_fft=400, hop=160):
            super().__init__()
            self.fbank, self.emb, self.cls = mods.compute_features.compute_fbanks, mods.embedding_model, mods.classifier
            self.n_fft, self.hop = n_fft, hop
            n = torch.arange(n_fft, dtype=torch.float64)
            k = torch.arange(n_fft // 2 + 1, dtype=torch.float64)[:, None]
            w = torch.hamming_window(n_fft, dtype=torch.float64)            # periodic, как в speechbrain STFT
            ang = 2 * torch.pi * k * n / n_fft
            kern = torch.cat([torch.cos(ang) * w, -torch.sin(ang) * w])[:, None, :].float()
            self.register_buffer("kern", kern)

        def forward(self, wav):                               # [1, N] float32, 16 kHz
            x = torch.nn.functional.pad(wav[:, None, :], (self.n_fft // 2, self.n_fft // 2))   # center, constant
            spec = torch.nn.functional.conv1d(x, self.kern, stride=self.hop)                  # [1, 2F, T]
            re, im = spec.chunk(2, dim=1)
            mag = (re * re + im * im).transpose(1, 2)                                         # [1, T, F]
            f = self.fbank(mag)
            f = f - f.mean(dim=1, keepdim=True)               # InputNormalization(sentence, std_norm=False)
            return self.cls(self.emb(f)).squeeze(1)           # [1, 107] log-probs

    model = LID(sb.mods).eval()
    wav = torch.randn(1, 16000 * 5)
    with torch.inference_mode():                              # свёрточный STFT = speechbrain Fbank
        a = sb.mods.compute_features(wav)
        b = model.fbank((lambda s: (s[0] ** 2 + s[1] ** 2).transpose(1, 2))(
            torch.nn.functional.conv1d(torch.nn.functional.pad(wav[:, None, :], (200, 200)), model.kern,
                                       stride=160).chunk(2, dim=1)))
        print("fbank max abs diff:", float((a - b).abs().max()))
    torch.onnx.export(model, (wav,), os.path.join(out, "lid.onnx"), input_names=["wav"], output_names=["logp"],
                      dynamic_axes={"wav": {1: "n"}}, opset_version=17, dynamo=False)
    labels = [sb.hparams.label_encoder.decode_ndim(i).split(":")[0].strip() for i in range(107)]
    with open(os.path.join(out, "lid_labels.txt"), "w") as f:
        f.write("\n".join(labels) + "\n")
    with torch.inference_mode():                              # reference for the ONNX check
        ref = model(wav).numpy()
    import numpy as np
    import onnxruntime as rt
    got = rt.InferenceSession(os.path.join(out, "lid.onnx")).run(None, {"wav": wav.numpy()})[0]
    print("lid max abs diff vs torch:", float(np.abs(ref - got).max()))
    shutil.rmtree(os.path.join(out, "sb"), ignore_errors=True)


if __name__ == "__main__":
    what, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    torch.set_num_threads(2)
    {"gigaam": gigaam, "gigaam-quantize": gigaam_quantize, "lid": lid}[what](out)
