"""Extract the supplied ReportLab PDFs without requiring a native PDF utility."""

import base64
import re
import sys
import zlib
from pathlib import Path


def literal_bytes(value):
    result = bytearray()
    i = 0
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while i < len(value):
        c = value[i]
        i += 1
        if c == 92 and i < len(value):
            c = value[i]
            i += 1
            if 48 <= c <= 55:
                digits = bytes([c])
                while i < len(value) and len(digits) < 3 and 48 <= value[i] <= 55:
                    digits += bytes([value[i]])
                    i += 1
                result.append(int(digits, 8))
                continue
            c = escapes.get(c, c)
        result.append(c)
    return bytes(result)


for filename in sys.argv[1:]:
    print("\nSOURCE:", Path(filename).name)
    raw = Path(filename).read_bytes()
    for match in re.finditer(rb"(?<!end)stream\r?\n(.*?)endstream", raw, re.S):
        try:
            stream = zlib.decompress(base64.a85decode(match[1].strip(), adobe=True))
        except Exception:
            continue
        for token in re.finditer(rb"\(((?:\\[\s\S]|[^\\)])*)\)\s*Tj", stream):
            data = literal_bytes(token[1])
            try:
                print(data.decode("utf-16-be"))
            except UnicodeDecodeError:
                print(data.decode("latin1"))
