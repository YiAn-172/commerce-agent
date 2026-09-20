from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

import onnx
import torch
import yaml
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from apps.intent_service.training.common import LABELS, directory_sha256, write_json
from packages.data_pipeline.provenance import sha256_file


class LogitsOnly(torch.nn.Module):
    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        token_type_ids: torch.Tensor,
    ) -> torch.Tensor:
        output = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        return cast(torch.Tensor, output.logits)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the selected MacBERT model to ONNX")
    parser.add_argument("--checkpoint", type=Path, default=Path("models/intent_classifier/best"))
    parser.add_argument("--config", type=Path, default=Path("configs/intent/macbert_v1.yaml"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/intent_classifier/best/onnx/model.onnx"),
    )
    parser.add_argument("--report", type=Path, default=Path("reports/training/onnx_export_v1.json"))
    parser.add_argument("--opset", type=int, default=17)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config: dict[str, Any] = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if not (args.checkpoint / "model.safetensors").exists():
        raise SystemExit(f"Checkpoint is incomplete: {args.checkpoint}")
    if not (args.checkpoint / "calibration.json").exists():
        raise SystemExit("Run calibration before export so the runtime package is complete")

    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint)
    model = AutoModelForSequenceClassification.from_pretrained(args.checkpoint)
    model.eval()
    wrapped = LogitsOnly(model)
    encoded = tokenizer(
        "查询订单物流进度",
        return_tensors="pt",
        max_length=int(config["max_length"]),
        truncation=True,
        padding="max_length",
    )
    if "token_type_ids" not in encoded:
        encoded["token_type_ids"] = torch.zeros_like(encoded["input_ids"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode():
        torch.onnx.export(
            wrapped,
            (
                encoded["input_ids"],
                encoded["attention_mask"],
                encoded["token_type_ids"],
            ),
            args.output,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["logits"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "token_type_ids": {0: "batch", 1: "sequence"},
                "logits": {0: "batch"},
            },
            opset_version=args.opset,
            do_constant_folding=True,
            dynamo=False,
        )
    onnx_model = onnx.load(args.output)
    onnx.checker.check_model(onnx_model)
    output_names = [value.name for value in onnx_model.graph.output]
    input_names = [value.name for value in onnx_model.graph.input]
    report = {
        "schema_version": "1.0",
        "checkpoint": args.checkpoint.as_posix(),
        "checkpoint_sha256": directory_sha256(args.checkpoint),
        "selection": json.loads((args.checkpoint / "selection.json").read_text(encoding="utf-8")),
        "config_sha256": sha256_file(args.config),
        "onnx_path": args.output.as_posix(),
        "onnx_sha256": sha256_file(args.output),
        "onnx_bytes": args.output.stat().st_size,
        "opset": args.opset,
        "inputs": input_names,
        "outputs": output_names,
        "labels": LABELS,
        "checker_passed": True,
    }
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
