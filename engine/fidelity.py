# -*- coding: utf-8 -*-
"""변환 결과를 스스로 재는 두 가지 검사.

1. 한컴 미리보기 대조 (preview_match)
   한컴오피스는 문서를 저장할 때 1쪽 그림(PrvImage, 약 724×1024)을 문서 안에 넣어 둔다.
   한컴이 직접 그린 것이므로 이 PC 에 한컴이 없어도 '정답지' 로 쓸 수 있다.
   우리 PDF 의 1쪽을 같은 크기로 그려 잉크 분포를 맞대 본다.
   글꼴 대체와 몇 화소의 줄 밀림은 어쩔 수 없으므로 쪽 전체가 아니라 작은 칸마다
   ±16화소 안에서 가장 잘 맞는 자리를 찾아 비교한다(칸 단위 정렬). 단 우리 렌더링에서
   내용이 빠지거나 단이 무너지면 어느 자리에서도 맞지 않아 점수가 내려간다.
   실측(2026-10-10): rhwp 0.80~0.93, 단이 무너진 LibreOffice+H2Orestart 0.35~0.72.

2. 글자층 회수율 (text_recall)
   rhwp 가 문서에서 직접 뽑은 본문(export-text)을 기준으로, 완성된 PDF 에서 pdfium 이
   뽑아낸 글자에 한글 글자·한글 낱말이 얼마나 남았는지 센다. 순서는 따지지 않는다(단·표의
   읽기 순서는 추출기마다 다르다). 기준 본문에는 수식 스크립트가 섞여 있어 한글만 센다.
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np

HANGUL = re.compile(r"[가-힣]")


def _recall(ref: Counter, cand: Counter) -> float | None:
    total = sum(ref.values())
    if not total:
        return None
    return sum((ref & cand).values()) / total


def text_recall(reference: str, extracted: str) -> tuple[float | None, float | None]:
    """(한글 글자 회수율, 한글 낱말 회수율). 기준에 한글이 없으면 None."""
    rc = _recall(Counter(HANGUL.findall(reference)), Counter(HANGUL.findall(extracted)))
    words = lambda t: Counter(w for w in t.split() if HANGUL.search(w))  # noqa: E731
    rw = _recall(words(reference), words(extracted))
    return rc, rw


# ── 한컴 미리보기 대조 ──────────────────────────────────────────────

TILE = 64          # 칸 크기 (축소 화면 기준 화소)
REACH = 8          # 칸마다 찾아볼 어긋남 범위 (±, 축소 화면 기준)
SCALE = 0.5        # 미리보기(약 724×1024)를 반으로 줄여 비교한다 — 속도, 그리고 글꼴 차이를 흐린다


def _ink(img) -> np.ndarray:
    from PIL import ImageFilter
    g = img.convert("L").filter(ImageFilter.GaussianBlur(0.75))
    return 1.0 - np.asarray(g, dtype=np.float32) / 255.0


def _one_way(a: np.ndarray, b: np.ndarray) -> float:
    """a 의 칸마다 b 의 주변(±REACH)에서 가장 잘 맞는 자리의 상관계수를, a 의 잉크량으로 가중평균."""
    from numpy.lib.stride_tricks import sliding_window_view
    H, W = a.shape
    T, R = TILE, REACH
    pad = np.pad(b, R, mode="constant")
    num = den = 0.0
    for y in range(0, H - T + 1, T // 2):
        for x in range(0, W - T + 1, T // 2):
            ta = a[y:y + T, x:x + T]
            wa = float(ta.sum())
            if wa < 2.0:          # 거의 빈 칸 — 근거가 없다
                continue
            za = ta - ta.mean()
            na = float(np.sqrt((za * za).sum()))
            if na < 1e-6:
                continue
            region = pad[y:y + T + 2 * R, x:x + T + 2 * R]
            win = sliding_window_view(region, (T, T))            # (2R+1, 2R+1, T, T)
            mb = win.mean(axis=(2, 3), keepdims=True)
            zb = win - mb
            nb = np.sqrt((zb * zb).sum(axis=(2, 3)))
            c = (zb * za).sum(axis=(2, 3)) / (na * np.maximum(nb, 1e-6))
            num += wa * max(float(c.max()), 0.0)
            den += wa
    return num / den if den else 0.0


def preview_match(pdf_path, preview_path) -> float | None:
    """1쪽과 한컴 미리보기의 일치도(0~1). 비교할 수 없으면 None."""
    import pypdfium2 as pdfium
    from PIL import Image
    try:
        prev = Image.open(preview_path)
        prev.load()
    except Exception:
        return None
    pw, ph = prev.size
    if pw < 100 or ph < 100:
        return None
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[0]
        w, h = page.get_size()
        # 미리보기와 쪽의 가로세로 비가 크게 다르면 1쪽이 아니거나 잘린 그림이다
        if abs((pw / ph) - (w / h)) > 0.03 * (w / h):
            return None
        size = (max(1, int(pw * SCALE)), max(1, int(ph * SCALE)))
        img = page.render(scale=pw / w).to_pil().convert("RGB")
    finally:
        pdf.close()
    a = _ink(prev.convert("RGB").resize(size, Image.LANCZOS))
    b = _ink(img.resize(size, Image.LANCZOS))
    if a.sum() < 5 or b.sum() < 5:        # 빈 쪽 — 비교할 잉크가 없다
        return None
    s1, s2 = _one_way(a, b), _one_way(b, a)
    if s1 + s2 <= 0:
        return 0.0
    return 2 * s1 * s2 / (s1 + s2)
