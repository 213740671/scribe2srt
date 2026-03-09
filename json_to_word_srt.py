#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path
from typing import Any

from core.srt_processor import create_word_level_srt_from_json


def is_transcript_json(data: dict[str, Any]) -> bool:
    words_obj = data.get("words")
    if not isinstance(words_obj, list):
        return False

    words: list[Any] = words_obj
    for item in words:
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "word":
                return True

    return False


def convert_one_json(in_path: Path) -> tuple[Path, int]:
    out_path = in_path.with_suffix(".srt")

    data: dict[str, Any] = json.loads(in_path.read_text(encoding="utf-8"))
    if not is_transcript_json(data):
        raise ValueError("不是包含 words 词级数据的转录 JSON")

    srt_data = create_word_level_srt_from_json(data)
    if not srt_data:
        raise ValueError("未找到可用的词级数据（words/type/start/end/text）")

    out_path.write_text(srt_data, encoding="utf-8")
    word_count = sum(1 for item in data.get("words", []) if item.get("type") == "word")
    return out_path, word_count


def main():
    current_dir = Path(__file__).resolve().parent
    candidate_files = sorted(current_dir.glob("*.json"))
    json_files: list[Path] = []

    for path in candidate_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if is_transcript_json(data):
            json_files.append(path)

    if not json_files:
        print(f"未找到包含词级数据的转录 JSON 文件: {current_dir}")
        return

    success = 0
    failed = 0

    for in_path in json_files:
        try:
            out_path, count = convert_one_json(in_path)
            success += 1
            print(f"完成: {out_path} ({count} 条)")
        except Exception as e:
            failed += 1
            print(f"跳过: {in_path} -> {e}")

    print(f"处理结束：成功 {success}，失败 {failed}，总计 {len(json_files)}")


if __name__ == "__main__":
    main()
