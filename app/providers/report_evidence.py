"""Bind model-selected evidence references to exact excerpts of the stored answers."""

import copy
import re

from app.core.errors import require


def prepare_report_evidence(payload, schema):
    request, contract = copy.deepcopy(payload), copy.deepcopy(schema)
    catalog: dict[str, tuple[str, str]] = {}
    for turn_index, turn in enumerate(request["turns"], 1):
        answer = turn.pop("answer")
        segments: list[dict[str, str]] = []
        for sentence in re.findall(r".+?(?:[。！？!?；;\n]+|$)", answer, re.DOTALL):
            for offset in range(0, len(sentence), 240):
                quote = sentence[offset : offset + 240].strip()
                if quote:
                    reference = f"T{turn_index}E{len(segments) + 1}"
                    catalog[reference] = (turn["turn_id"], quote)
                    segments.append({"evidence_id": reference, "text": quote})
        turn["answer_segments"] = segments
    for definition in ("Dimension", "Review"):
        evidence = contract["$defs"][definition]["properties"]["evidence"]
        if catalog:
            evidence["items"] = {"type": "string", "enum": list(catalog)}
        else:
            evidence["maxItems"] = 0
    return request, contract, catalog


def resolve_report_evidence(data, catalog):
    require(isinstance(data, dict), "AI_OUTPUT_INVALID", "报告结构无效", 502)
    for collection in ("reviews", "dimensions"):
        items = data.get(collection)
        require(isinstance(items, list), "AI_OUTPUT_INVALID", "报告点评或维度结构无效", 502)
        for item in items:
            require(isinstance(item, dict), "AI_OUTPUT_INVALID", "报告条目结构无效", 502)
            references = item.get("evidence", [])
            require(isinstance(references, list), "AI_OUTPUT_INVALID", "报告证据结构无效", 502)
            quotes = []
            for reference in references:
                require(
                    isinstance(reference, str) and reference in catalog,
                    "AI_OUTPUT_INVALID",
                    "报告引用了未知证据编号",
                    502,
                )
                turn_id, quote = catalog[reference]
                require(
                    collection != "reviews" or item.get("turn_id") == turn_id,
                    "AI_OUTPUT_INVALID",
                    "逐题证据引用了其他回答",
                    502,
                )
                quotes.append(quote)
            item["evidence"] = list(dict.fromkeys(quotes))
    return data
