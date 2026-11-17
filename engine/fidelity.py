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

3. 실루엣 일치율 (silhouette) — 원본_대조.py 가 쓰는 엄격한 지표
   rhwp 개발진의 Visual Sweep 과 같은 식이다: 96dpi, 내용 화소 = RGB 최소값 < 232,
   2px 안에 상대 쪽 내용이 있으면 일치, 경계의 비슷한 색은 일치로 본다. 칸 단위 정렬이
   없어서 몇 화소의 밀림도 그대로 깎인다 — 조판 차이를 고칠 때의 잣대다.

한컴 미리보기는 내용이 가로세로 0.58% 작게 그려져 있다(PREVIEW_SQUASH, 왼쪽 위 기준).
정밀한 한컴 PDF 와 맞대면 쪽 전체에서 어긋남이 0±2px 인데 미리보기와는 아래·오른쪽으로 갈수록
일정한 비율로 벌어진다 — 세로는 사용자 문서 3개·126개 띠의 회귀 dy = 0.07 + 0.00581·y,
가로는 문서 4개 모두 왼쪽 단 +1px·오른쪽 단 +3px(0.55%). 미리보기와 맞댈 때는 우리 그림을
같은 비율로 줄여 비교한다.
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

PREVIEW_SQUASH = 0.99423   # 한컴 미리보기의 축소 비율, 가로세로 같음 (모듈 설명 참조)

TILE = 64          # 칸 크기 (축소 화면 기준 화소)
REACH = 8          # 칸마다 찾아볼 어긋남 범위 (±, 축소 화면 기준)
# 미리보기(약 724×1024)를 반으로 줄여 비교한다 — 속도, 그리고 글꼴 차이를 흐린다.
# 옛 한컴의 작은 미리보기(177×250)도 같은 비율로 줄인다. 칸·범위를 크기에 맞춰 줄여 보니(짧은 변 362
# 기준) 작은 미리보기만 엄격해져 확인 권장이 17→25개로 늘었는데, 늘어난 문서를 열어 보면 대부분 한컴
# 미리보기 글자가 진한 검정이고 우리 것은 흐린 회색인 차이였다(공개 문서 312개 실측) — 되돌렸다.
SCALE = 0.5


def _ink(img) -> np.ndarray:
    from PIL import ImageFilter
    g = img.convert("L").filter(ImageFilter.GaussianBlur(0.75))
    return 1.0 - np.asarray(g, dtype=np.float32) / 255.0


def _one_way(a: np.ndarray, b: np.ndarray) -> float | None:
    """a 의 칸마다 b 의 주변(±REACH)에서 가장 잘 맞는 자리의 상관계수를, a 의 잉크량으로 가중평균.

    잴 칸이 하나도 없으면(그림이 칸보다 작거나 칸마다 잉크가 거의 없으면) None — 0 은 '전혀 다름' 이다.
    """
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
    return num / den if den else None


def _load_preview(preview_path):
    from PIL import Image
    try:
        prev = Image.open(preview_path)
        prev.load()
    except Exception:
        return None
    if prev.size[0] < 100 or prev.size[1] < 100:
        return None
    return prev.convert("RGB")


def render_like_preview(pdf_path, prev, squash: bool = True):
    """PDF 1쪽을 미리보기와 같은 크기로 그린다(한컴 미리보기처럼 0.58% 줄여서).

    미리보기와 쪽의 가로세로 비가 크게 다르면 1쪽이 아니거나 잘린 그림이라 None.
    """
    import pypdfium2 as pdfium
    from PIL import Image
    pw, ph = prev.size
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        page = pdf[0]
        w, h = page.get_size()
        if abs((pw / ph) - (w / h)) > 0.03 * (w / h):
            return None
        img = page.render(scale=pw / w).to_pil().convert("RGB").resize(prev.size, Image.LANCZOS)
    finally:
        pdf.close()
    if squash:
        pressed = img.resize((max(1, round(pw * PREVIEW_SQUASH)), max(1, round(ph * PREVIEW_SQUASH))),
                             Image.LANCZOS)
        img = Image.new("RGB", prev.size, "white")
        img.paste(pressed, (0, 0))
    return img


def preview_match(pdf_path, preview_path) -> float | None:
    """1쪽과 한컴 미리보기의 일치도(0~1, 칸 단위 정렬). 비교할 수 없으면 None."""
    from PIL import Image
    prev = _load_preview(preview_path)
    if prev is None:
        return None
    img = render_like_preview(pdf_path, prev)
    if img is None:
        return None
    pw, ph = prev.size
    size = (max(1, int(pw * SCALE)), max(1, int(ph * SCALE)))
    a = _ink(prev.resize(size, Image.LANCZOS))
    b = _ink(img.resize(size, Image.LANCZOS))
    if a.sum() < 5 or b.sum() < 5:        # 빈 쪽 — 비교할 잉크가 없다
        return None
    s1, s2 = _one_way(a, b), _one_way(b, a)
    if s1 is None or s2 is None:          # 잴 칸이 없다 — 비교할 수 없음
        return None
    if s1 + s2 <= 0:
        return 0.0
    return 2 * s1 * s2 / (s1 + s2)


# ── 실루엣 일치율 ───────────────────────────────────────────────────

def silhouette(a, b, radius: int = 2) -> float:
    """두 그림의 2px 관용 내용 실루엣 일치율(%). rhwp Visual Sweep 과 같은 식.

    내용 화소는 RGB 최소값 < 232. 한쪽 내용 화소 주변 radius 안에 상대 쪽 내용이 없으면
    불일치다. 두 쪽 모두 흰 배경(≥244)이 아니고 색 차이가 32 이하인 경계 화소는 일치로 친다.
    양쪽 모두 내용이 없으면 100.
    """
    from PIL import Image, ImageChops, ImageFilter
    a, b = a.convert("RGB"), b.convert("RGB")
    W, H = max(a.width, b.width), max(a.height, b.height)
    if a.size != (W, H):
        x = Image.new("RGB", (W, H), "white"); x.paste(a); a = x
    if b.size != (W, H):
        x = Image.new("RGB", (W, H), "white"); x.paste(b); b = x

    def mins(im):
        r, g, bb = im.split()
        return ImageChops.darker(ImageChops.darker(r, g), bb)

    amin, bmin = mins(a), mins(b)
    am = amin.point([255 if v < 232 else 0 for v in range(256)])
    bm = bmin.point([255 if v < 232 else 0 for v in range(256)])
    k = radius * 2 + 1
    an, bn = am.filter(ImageFilter.MaxFilter(k)), bm.filter(ImageFilter.MaxFilter(k))
    union = ImageChops.lighter(am, bm).histogram()[255]
    if not union:
        return 100.0
    unresolved = ImageChops.lighter(ImageChops.subtract(am, bn), ImageChops.subtract(bm, an))
    raw = unresolved.histogram()[255]
    visible = ImageChops.multiply(amin.point([255 if v < 244 else 0 for v in range(256)]),
                                  bmin.point([255 if v < 244 else 0 for v in range(256)]))
    d = [ImageChops.difference(x, y) for x, y in zip(a.split(), b.split())]
    md = ImageChops.lighter(ImageChops.lighter(d[0], d[1]), d[2])
    similar = md.point([255 if v <= 32 else 0 for v in range(256)])
    support = ImageChops.multiply(ImageChops.difference(am, bm), ImageChops.multiply(visible, similar))
    reconciled = ImageChops.multiply(unresolved, support).histogram()[255]
    return (1.0 - (raw - reconciled) / union) * 100.0


def preview_silhouette(pdf_path, preview_path) -> float | None:
    """1쪽과 한컴 미리보기의 실루엣 일치율(%). 미리보기 해상도(약 70~90dpi)에서 잰다."""
    prev = _load_preview(preview_path)
    if prev is None:
        return None
    img = render_like_preview(pdf_path, prev)
    return None if img is None else silhouette(img, prev)
