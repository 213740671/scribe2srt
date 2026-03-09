from __future__ import annotations

import json
import os
from typing import Any, Callable

from .srt_processor import create_srt_from_json


class TranscriptPersistenceHelper:
    def __init__(self, logger: Callable[[str], None]):
        self._log = logger

    def merge_transcript(
        self,
        base_transcript: dict[str, Any],
        incoming_transcript: dict[str, Any],
        *,
        apply_offset: float = 0.0,
    ) -> dict[str, Any]:
        if not base_transcript:
            if apply_offset:
                transcript_copy = incoming_transcript.copy()
                words = transcript_copy.get("words", [])
                for word in words:
                    word["start"] = round(word["start"] + apply_offset, 3)
                    word["end"] = round(word["end"] + apply_offset, 3)
                return transcript_copy
            return incoming_transcript.copy()

        words = incoming_transcript.get("words", [])
        if apply_offset:
            for word in words:
                word["start"] = round(word["start"] + apply_offset, 3)
                word["end"] = round(word["end"] + apply_offset, 3)

        base_transcript.setdefault("words", [])
        base_transcript.setdefault("text", "")
        base_transcript["words"].extend(words)
        incoming_text = incoming_transcript.get("text", "")
        if incoming_text:
            if base_transcript["text"]:
                base_transcript["text"] += " "
            base_transcript["text"] += incoming_text
        return base_transcript

    def save_segment_json(self, chunk_path: str, transcript_json: dict[str, Any]):
        base_chunk_path, _ = os.path.splitext(chunk_path)
        segment_json_path = base_chunk_path + ".json"
        with open(segment_json_path, "w", encoding="utf-8") as f:
            json.dump(transcript_json, f, ensure_ascii=False, indent=4)
        self._log(f"分段转录JSON已保存到: {os.path.basename(segment_json_path)}")

    def write_final_outputs(
        self,
        original_file_path: str,
        combined_transcript: dict[str, Any],
        max_subtitle_duration: float,
        subtitle_settings: dict[str, Any],
    ) -> tuple[bool, str]:
        base_path, _ = os.path.splitext(original_file_path)
        output_json_path = base_path + ".json"
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(combined_transcript, f, ensure_ascii=False, indent=4)
        self._log(f"合并后的转录文本已保存到:\n{output_json_path}")

        self._log("正在生成SRT字幕文件...")
        srt_data = create_srt_from_json(
            combined_transcript,
            max_subtitle_duration=max_subtitle_duration,
            subtitle_settings=subtitle_settings,
        )
        if not srt_data:
            return False, "从合并后的JSON生成SRT失败。"

        output_srt_path = base_path + ".srt"
        with open(output_srt_path, "w", encoding="utf-8") as f:
            f.write(srt_data)
        self._log(f"最终SRT字幕文件已保存到:\n{output_srt_path}")
        return True, output_srt_path
