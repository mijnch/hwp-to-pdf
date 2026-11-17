# -*- coding: utf-8 -*-
"""rhwp 가 만든 PDF 의 글자층을 바로잡는다. 화면에 보이는 모습은 바꾸지 않는다.

왜 필요한가 (2026-10-10 실측, rhwp 0.8.7 svg 백엔드)
  rhwp 는 쪽 전체를 1/971 배율의 Form XObject 하나로 감싸고, 그 안에서 글자를
  한 자씩 따로 그린다(글자마다 BT … ET). 보기에는 정확하지만 글자를 뽑아 보면
    - pdfium(Chrome·Edge 뷰어, PDF OCR 도구): 앞의 다섯 글자 안에 같은 글자가 있으면
      '가짜 굵게' 겹침으로 보고 버린다 → "대학물리" 가 "대 물리" 로. 한글 글자 10~40% 탈락
    - pdfminer: 띄어쓰기·줄바꿈이 하나도 없다
    - MuPDF·pypdf: 띄어쓰기 대부분이 사라진다 (공백 글자를 그리지 않으므로)
  Claude 가 PDF 를 읽을 때 어느 추출기를 쓰든 본문이 깨지지 않게 하는 것이 목적이다.

무엇을 하는가 (둘 다 렌더링 결과는 픽셀 단위로 같다 — 단위·종단 검증이 확인한다)
  1. 쪽을 감싼 Form 을 쪽 본문으로 펼친다: 같은 변환 행렬 + BBox 클립.
     pdfium 의 글자 탈락과 pdfminer 의 줄·띄어쓰기 소실이 이것만으로 사라진다.
  2. rhwp 렌더 트리(줄·낱말 묶음의 원문, 공백 포함)와 글자 위치를 맞춰 보고,
     띄어쓰기 자리에 보이지 않는 공백 글자(Tr 3, 표준 Helvetica)를 끼워 넣는다.
     MuPDF·pypdf 도 띄어쓰기를 되찾는다.

글자 객체를 합치는 방법(TJ 한 줄로 묶기)도 시험했으나 기각했다 — 렌더링이 미세하게
바뀌었고(쪽당 1~2천 화소), 띄어쓰기는 여전히 추출기의 간격 추정에 맡겨야 했다.
ActualText 로 감싸는 방법은 pdfium 만 고치고 MuPDF 에서는 글자를 중복시켰다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pikepdf

SPACE_FONT = "/HwpSp"   # 끼워 넣는 공백 전용 글꼴 자원 이름 (rhwp 의 /fo0… 과 겹치지 않게)
SPACE_ADV = 0.278       # Helvetica 공백 폭(글자 크기 대비)

# 글자 객체를 감싼 q…Q 묶음 안에 와도 되는 연산자 — 이것 말고 무언가를 그리는 연산자가
# 끼어 있으면 그 묶음은 '글자 하나'가 아니다.
_STATE_OPS = {"q", "cs", "CS", "scn", "SCN", "sc", "SC", "g", "G", "rg", "RG", "k", "K",
              "w", "M", "J", "j", "d", "Tr", "gs", "ri", "i", "cm"}
_TEXT_STATE_OPS = {"Tc", "Tw", "Tz", "TL", "Ts", "Td", "TD", "T*"}


def mul(a, b):
    """행렬 곱 a×b (PDF 행벡터 관례: 점 p 에 a 를 먼저, 그다음 b 를 적용)."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def font_widths(font) -> tuple[dict[int, float], float]:
    """Type0/CIDFontType2 글꼴의 CID → 폭(1/1000 em). 단순 글꼴이면 빈 표."""
    try:
        d = font.DescendantFonts[0]
    except (AttributeError, IndexError, KeyError, TypeError):
        return {}, 1000.0
    dw = float(d.get("/DW", 1000))
    w: dict[int, float] = {}
    arr = list(d.get("/W", []))
    i = 0
    while i + 1 < len(arr):
        first = int(arr[i])
        if isinstance(arr[i + 1], pikepdf.Array):
            for k, v in enumerate(arr[i + 1]):
                w[first + k] = float(v)
            i += 2
        elif i + 2 < len(arr):
            last, v = int(arr[i + 1]), float(arr[i + 2])
            for c in range(first, last + 1):
                w[c] = v
            i += 3
        else:
            break
    return w, dw


@dataclass
class Run:
    """렌더 트리의 TextRun — 같은 글자 모양으로 이어진 낱말 묶음(공백 포함 원문)."""
    text: str
    x: float
    y: float
    w: float
    h: float


def tree_runs(tree: dict) -> list[Run]:
    out: list[Run] = []

    def walk(n):
        if n.get("type") == "TextRun" and isinstance(n.get("text"), str):
            b = n.get("bbox") or {}
            out.append(Run(n["text"], float(b.get("x", 0)), float(b.get("y", 0)),
                           float(b.get("w", 0)), float(b.get("h", 0))))
        for c in n.get("children", ()):
            walk(c)

    walk(tree)
    return out


@dataclass
class Glyph:
    """글자 하나를 그리는 q…Q 묶음. start/end 는 명령 목록의 위치(끝 포함)."""
    start: int
    end: int
    outer: int                    # 묶음 바깥의 q 깊이
    octm: list                    # 묶음 바깥의 변환 행렬 (흐름 공간 기준)
    tm: int                       # Tm 명령 위치
    tf: int                       # Tf 명령 위치
    tj: int                       # Tj 명령 위치
    x: float = 0.0                # 글자 원점 — 묶음 바깥 공간
    y: float = 0.0
    a: float = 1.0                # 글자 행렬의 가로·세로 성분 (장평·뒤집기)
    d: float = 1.0
    font: str = ""
    size: float = 0.0
    cid: int = 0
    tx: float = 0.0               # 글자 원점 — 렌더 트리 좌표(px)
    ty: float = 0.0
    run: int | None = None
    extra: dict = field(default_factory=dict)


def find_glyphs(ins) -> list[Glyph]:
    """명령 목록에서 '글자 하나를 그리는 q…Q 묶음'을 찾는다.

    묶음의 바깥 경계는, 그 BT 를 감싼 q 들 가운데 직전 그리기 명령 뒤에 열린 가장 바깥
    q 다. 그 q 가 닫히는 Q 까지가 한 글자다. 그 사이에 그리기 명령이 끼면 버린다.
    """
    glyphs: list[Glyph] = []
    depth = 0
    open_q: dict[int, int] = {}
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    stack: list[list] = []
    ctm_at: dict[int, list] = {}
    barrier = -1
    cur: dict | None = None      # 닫히기를 기다리는 글자 묶음
    bt: dict | None = None
    for k, i in enumerate(ins):
        op = str(i.operator)
        if op == "q":
            open_q[depth] = k
            ctm_at[depth] = ctm[:]
            stack.append(ctm[:])
            depth += 1
        elif op == "Q":
            depth -= 1
            ctm = stack.pop() if stack else [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            if cur is not None and depth == cur["outer"]:
                glyphs.append(Glyph(start=cur["start"], end=k, outer=cur["outer"],
                                    octm=cur["octm"], tm=cur["tm"], tf=cur["tf"], tj=cur["tj"]))
                cur = None
                barrier = k
            if depth < 0:        # 짝이 안 맞는 흐름 — 손대지 않는다
                return []
        elif op == "cm":
            try:
                ctm = mul([float(x) for x in i.operands], ctm)
            except (TypeError, ValueError):
                return []
        elif op == "BT":
            bt = {}
        elif op in ("Tm", "Tf") and bt is not None:
            bt[op.lower()] = k
        elif op in ("Tj", "TJ", "'", '"'):
            if bt is None or "tj" in bt or op != "Tj":
                barrier = k
                cur = None
                if bt is not None:
                    bt["bad"] = True
                continue
            bt["tj"] = k
        elif op == "ET":
            if (bt is None or bt.get("bad") or "tm" not in bt or "tf" not in bt
                    or "tj" not in bt or cur is not None):
                barrier = k
                cur = None
                bt = None
                continue
            lo = max(barrier, glyphs[-1].end if glyphs else -1)
            outer = None
            for dd in range(depth - 1, -1, -1):
                if open_q.get(dd, -1) > lo:
                    outer = dd
                else:
                    break
            if outer is None:
                barrier = k
            else:
                cur = dict(start=open_q[outer], outer=outer, octm=ctm_at[outer], **bt)
            bt = None
        elif op in _STATE_OPS or op in _TEXT_STATE_OPS:
            pass
        else:
            barrier = k
            cur = None
    return glyphs


def measure(ins, g: Glyph) -> bool:
    """글자의 위치·크기·글꼴을 채운다. 회전·기울임이 있거나 cm 이 둘 이상이면 False."""
    cms = [k for k in range(g.start, g.end + 1) if str(ins[k].operator) == "cm"]
    if len(cms) > 1:
        return False
    try:
        tm = [float(x) for x in ins[g.tm].operands]
        cm = [float(x) for x in ins[cms[0]].operands] if cms else [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        font, size = ins[g.tf].operands
        code = bytes(ins[g.tj].operands[0])
    except (TypeError, ValueError, IndexError):
        return False
    if len(tm) != 6 or len(cm) != 6 or any(abs(v) > 1e-9 for v in (tm[1], tm[2], cm[1], cm[2])):
        return False
    if len(code) != 2:           # rhwp 의 Identity-H 글꼴은 글자당 2바이트
        return False
    m = mul(tm, cm)
    g.a, g.d, g.x, g.y = m[0], m[3], m[4], m[5]
    g.font, g.size, g.cid = str(font), float(size), int.from_bytes(code, "big")
    return g.a > 0


def assign_runs(glyphs: list[Glyph], runs: list[Run]) -> None:
    """각 글자를 그것이 속한 낱말 묶음(TextRun)에 배정한다.

    상자 오른쪽 끝에 걸친 글자는 다음 묶음의 첫 글자다 — 왼쪽 끝을 기준으로 가장 가까운
    묶음을 고른다. 같은 자리에 같은 표가 두 번 그려진 문서(실측: 대학물리 시험지)도 있어
    이미 글자 수가 찬 묶음은 건너뛴다.
    """
    need = [len(re.sub(r"\s", "", r.text)) for r in runs]
    got = [0] * len(runs)
    for g in glyphs:
        best = None
        for ri, r in enumerate(runs):
            if not (r.x - 1 <= g.tx < r.x + r.w - 0.25 and r.y - 1 <= g.ty <= r.y + r.h + 3):
                continue
            if got[ri] >= need[ri]:
                continue
            if best is None or r.x > runs[best].x:
                best = ri
        g.run = best
        if best is not None:
            got[best] += 1


def plan_spaces(glyphs: list[Glyph], runs: list[Run]):
    """묶음 원문의 공백을 글자 사이에 배치한다.

    돌려주는 값: ({글자 묶음 끝 위치: (글자, 공백)}, {글자 묶음 시작 위치: (글자, 공백)}).
    묶음의 공백 아닌 글자 수와 그려진 글자 수가 다르면(합자·채움 문자 등) 그 묶음은 건너뛴다.
    """
    by_run: dict[int, list[Glyph]] = {}
    for g in glyphs:
        if g.run is not None:
            by_run.setdefault(g.run, []).append(g)
    after: dict[int, tuple[Glyph, str]] = {}
    before: dict[int, tuple[Glyph, str]] = {}
    matched = 0
    for ri, gs in by_run.items():
        text = runs[ri].text.replace("\t", " ")
        segs = re.findall(r"\S\s*", text.lstrip())
        if len(segs) != len(gs):
            continue
        matched += 1
        gs = sorted(gs, key=lambda g: g.tx)
        lead = text[:len(text) - len(text.lstrip())]
        for k, g in enumerate(gs):
            gap = segs[k][1:]
            if gap:
                after[g.end] = (g, gap)
            if k == 0 and lead:
                before[g.start] = (g, lead)
    return after, before, matched


def _space_ops(g: Glyph, spaces: str, widths, where: str):
    w, dw = widths.get(g.font, ({}, 1000.0))
    n = len(spaces)
    if where == "after":
        x = g.x + w.get(g.cid, dw) / 1000 * g.size * g.a
    else:
        x = g.x - SPACE_ADV * g.size * g.a * n
    op = pikepdf.Operator
    I = pikepdf.ContentStreamInstruction
    tm = [round(v, 4) for v in (g.a, 0, 0, g.d, x, g.y)]
    return [I([], op("q")), I([], op("BT")),
            I([pikepdf.Name(SPACE_FONT), round(g.size, 4)], op("Tf")),
            I([3], op("Tr")), I(tm, op("Tm")),
            # 원문의 공백 문자가 무엇이든(NBSP·전각 공백) 같은 수의 ASCII 공백으로 넣는다 — 그대로 넣으면
            # WinAnsi 로 못 적어 pikepdf 가 UTF-16(þÿ…) 문자열로 만들고, 추출한 글자에 'þÿ' 가 섞인다.
            I([pikepdf.String(" " * n)], op("Tj")),
            I([], op("ET")), I([], op("Q"))]


def add_spaces(ins, runs: list[Run], fonts, to_tree):
    """명령 목록에 보이지 않는 공백을 끼워 넣은 새 목록과 통계를 돌려준다.

    to_tree(x, y): 흐름 공간의 점 → 렌더 트리 좌표(px).
    """
    glyphs = [g for g in find_glyphs(ins) if measure(ins, g)]
    for g in glyphs:
        o = g.octm
        g.tx, g.ty = to_tree(g.x * o[0] + g.y * o[2] + o[4], g.x * o[1] + g.y * o[3] + o[5])
    assign_runs(glyphs, runs)
    after, before, matched = plan_spaces(glyphs, runs)
    widths = {name: font_widths(f) for name, f in fonts.items()}
    out = []
    n = 0
    for k, i in enumerate(ins):
        if k in before:
            out.extend(_space_ops(*before[k], widths, "before"))
            n += 1
        out.append(i)
        if k in after:
            out.extend(_space_ops(*after[k], widths, "after"))
            n += 1
    stats = dict(glyphs=len(glyphs), runs=len(runs), matched=matched, spaces=n)
    return out, stats


def space_font() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                              BaseFont=pikepdf.Name.Helvetica,
                              Encoding=pikepdf.Name.WinAnsiEncoding)


def page_form(page):
    """쪽이 'cm + Do(Form)' 하나로만 이루어졌으면 (cm 명령, Form) 을, 아니면 None."""
    try:
        ins = list(pikepdf.parse_content_stream(page))
    except pikepdf.PdfError:
        return None
    if [str(i.operator) for i in ins] != ["cm", "Do"]:
        return None
    try:
        form = page.Resources.XObject[ins[1].operands[0]]
    except (AttributeError, KeyError):
        return None
    if form.get("/Subtype") != "/Form":
        return None
    return ins[0], form


def fix_page(pdf: pikepdf.Pdf, page, tree: dict | None) -> dict:
    """한 쪽을 고친다. 고칠 수 없는 모양이면 손대지 않고 그 사유를 통계에 남긴다."""
    st = dict(flattened=False, glyphs=0, runs=0, matched=0, spaces=0)
    pf = page_form(page)
    if pf is None:
        st["skip"] = "쪽 구성이 예상과 다름"
        return st
    page_cm, form = pf
    bb = [float(x) for x in form.BBox]
    res = form.get("/Resources", pikepdf.Dictionary())
    ins = list(pikepdf.parse_content_stream(form))
    fonts = dict(res.Font.items()) if "/Font" in res else {}

    if tree is not None and fonts:
        H = bb[3]
        out, s = add_spaces(ins, tree_runs(tree), {str(k): v for k, v in fonts.items()},
                            lambda x, y: (x, H - y))
        st.update(s)
        if s["spaces"]:
            ins = out
            res.Font[SPACE_FONT] = space_font()

    # Form 을 펼친다: 쪽의 cm 과 Form 의 /Matrix 를 원래 값 그대로 차례로 걸고(미리 곱하면
    # 반올림 때문에 안티에일리어싱이 한 단계씩 달라진다 — 실측), Form 이 하던 BBox 클립을 직접 건다.
    I, op = pikepdf.ContentStreamInstruction, pikepdf.Operator
    head_ins = [I([], op("q")), page_cm]
    if "/Matrix" in form:
        head_ins.append(I(list(form.Matrix), op("cm")))
    head = pikepdf.unparse_content_stream(head_ins + [
        pikepdf.ContentStreamInstruction(list(form.BBox[:2]) + [bb[2] - bb[0], bb[3] - bb[1]],
                                         pikepdf.Operator("re")),
        pikepdf.ContentStreamInstruction([], pikepdf.Operator("W")),
        pikepdf.ContentStreamInstruction([], pikepdf.Operator("n")),
    ])
    body = pikepdf.unparse_content_stream(ins)
    page.Contents = pdf.make_stream(head + b"\n" + body + b"\nQ\n")
    page.Resources = res
    st["flattened"] = True
    return st
