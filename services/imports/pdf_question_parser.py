from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pdfplumber


QUESTION_RE = re.compile(r"(?m)^(?:QUESTION|问题)\s+(\d+)[ \t]*$")
ANSWER_RE = re.compile(r"(?m)^(?:Correct Answer|正确答案)[:：][ \t]*(.*)$")
OPTION_RE = re.compile(r"(?ms)^([A-Z])\.\s+(.*?)(?=^[A-Z]\.\s+|\Z)")
FIGURE_HINT_RE = re.compile(r"如图|图示|图中|拓扑", re.I)
WATERMARK_SIZE = (221, 60)


def clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\x00", "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def extract_explanation(block: str, answer_match: re.Match[str]) -> str:
    value = block[answer_match.end() :]
    value = re.sub(r"(?m)^(?:Section|章节)[:：].*$", "", value)
    value = re.sub(r"(?m)^(?:Explanation(?:/Reference:)?|说明/参考[:：]?)[ \t]*$", "", value)
    value = clean_text(value)
    return value if value and value != "--" else "来源 PDF 未提供解析，请人工补充后再核验。"


def parse_pdf(
    pdf_path: Path,
) -> tuple[list[dict], list[dict], dict[str, tuple[int, tuple[float, float, float, float], float]]]:
    anomalies: list[dict] = []
    best_images: dict[str, tuple[int, tuple[float, float, float, float], float]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
        page_starts: list[int] = []
        combined = ""
        for text in page_texts:
            page_starts.append(len(combined))
            combined += text + "\n\n"

        headers = list(QUESTION_RE.finditer(combined))
        records: list[dict] = []
        occurrences: defaultdict[int, int] = defaultdict(int)
        for index, header in enumerate(headers):
            number = int(header.group(1))
            occurrences[number] += 1
            occurrence = occurrences[number]
            record_key = f"{number}:{occurrence}"
            end = headers[index + 1].start() if index + 1 < len(headers) else len(combined)
            block = combined[header.end() : end]
            answer_match = ANSWER_RE.search(block)
            page_no = 1
            for page_index, start in enumerate(page_starts):
                if start <= header.start():
                    page_no = page_index + 1
                else:
                    break
            if not answer_match or not answer_match.group(1).strip():
                anomalies.append({"question": number, "page": page_no, "reason": "blank_answer"})
                continue

            raw_answer = clean_text(answer_match.group(1))
            before_answer = block[: answer_match.start()]
            option_matches = list(OPTION_RE.finditer(before_answer))
            if option_matches:
                options = [clean_text(match.group(2)) for match in option_matches]
                labels = [match.group(1) for match in option_matches]
                answer = "".join(re.findall(r"[A-Z]", raw_answer.upper()))
                if not answer.isalpha() or not set(answer).issubset(set(labels)):
                    anomalies.append(
                        {"question": number, "page": page_no, "reason": "invalid_answer", "answer": raw_answer}
                    )
                    continue
                stem = clean_text(before_answer[: option_matches[0].start()])
                stem = re.sub(r"^(?:★+[ \t]*\n?)+", "", stem).strip()
                qtype = "multiple" if len(answer) > 1 else "single"
                answers = list(answer)
            else:
                options = []
                stem = clean_text(before_answer)
                stem = re.sub(r"^(?:★+[ \t]*\n?)+", "", stem).strip()
                qtype = "fill"
                answers = [raw_answer]
            records.append(
                {
                    "number": number,
                    "occurrence": occurrence,
                    "key": record_key,
                    "page": page_no,
                    "type": qtype,
                    "stem": stem,
                    "options": options,
                    "answer": answers,
                    "explanation": extract_explanation(block, answer_match),
                }
            )

        active_question: str | None = None
        image_occurrences: defaultdict[int, int] = defaultdict(int)
        for page_index, page in enumerate(pdf.pages):
            events: list[tuple[float, int, object]] = []
            for found in page.search(r"(?:QUESTION|问题)\s+\d+", regex=True) or []:
                match = re.search(r"\d+", found["text"])
                if match:
                    events.append((float(found["top"]), 0, int(match.group())))
            for image in page.images:
                srcsize = tuple(image.get("srcsize") or ())
                if image.get("top", -1) < 0 or srcsize == WATERMARK_SIZE:
                    continue
                x0, top = max(0.0, float(image["x0"])), max(0.0, float(image["top"]))
                x1 = min(float(page.width), float(image["x1"]))
                bottom = min(float(page.height), float(image["bottom"]))
                if x1 <= x0 or bottom <= top:
                    continue
                area = (x1 - x0) * (bottom - top)
                events.append((top, 1, (page_index, (x0, top, x1, bottom), area)))
            for _, kind, payload in sorted(events, key=lambda event: (event[0], event[1])):
                if kind == 0:
                    question_number = int(payload)
                    image_occurrences[question_number] += 1
                    active_question = f"{question_number}:{image_occurrences[question_number]}"
                elif active_question is not None:
                    image_data = payload
                    previous = best_images.get(active_question)
                    if previous is None or image_data[2] > previous[2]:
                        best_images[active_question] = image_data

        complete_records: list[dict] = []
        for record in records:
            if record["stem"]:
                complete_records.append(record)
            elif record["key"] in best_images:
                record["stem"] = f"题干见附图（PDF QUESTION {record['number']}）"
                complete_records.append(record)
            else:
                anomalies.append({"question": record["number"], "page": record["page"], "reason": "blank_stem"})

    return complete_records, anomalies, best_images
