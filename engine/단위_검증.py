# -*- coding: utf-8 -*-
"""엔진 로직 단위 검증 — rhwp 없이, 가짜 입력으로 즉시 확인한다.

pikepdf·numpy·Pillow·pypdfium2 가 필요하다(engine\\venv 에 있다). 실제 문서는 쓰지 않는다.

    engine\\venv\\Scripts\\python engine\\단위_검증.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import pikepdf  # noqa: E402
from pikepdf import ContentStreamInstruction as I, Operator as Op, Name, String  # noqa: E402

import fidelity  # noqa: E402
import hwp2pdf as H  # noqa: E402
import pdf_text as P  # noqa: E402
import rhwp_cli as R  # noqa: E402

fails, passed = [], 0


def check(name, cond, detail=""):
    global passed
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + str(detail) if detail else ''}")
    if cond:
        passed += 1
    else:
        fails.append(name)


TMP = Path(tempfile.mkdtemp(prefix="hwp2pdf_unit_"))
H.LOG_FILE = TMP / "실행기록.txt"          # 경고 기록이 도구 폴더로 가지 않게


# ── 설정 ────────────────────────────────────────────────────────────
print("설정")
cfgf = TMP / "설정.ini"
check("설정 파일이 없으면 기본값", H.load_config(TMP / "없음.ini") == H.DEFAULTS)
cfgf.write_text("[settings]\n저장_위치 = 원본 옆\n미리보기_대조 = 끔\n", encoding="utf-8")
c = H.load_config(cfgf)
check("원본옆(띄어 써도)·끔 을 읽음", c["저장_위치"] == "원본옆" and c["미리보기_대조"] is False, c)
cfgf.write_text("[Settings]\n저장_위치 = 어딘가\n미리보기_대조 = 아마도\n", encoding="utf-8")
c = H.load_config(cfgf)
check("모르는 값은 기본값으로, 섹션 이름 대소문자 무시", c == H.DEFAULTS, c)
check("모르는 값은 기록에 남김", "어딘가" in H.LOG_FILE.read_text(encoding="utf-8"))
cfgf.write_bytes("[settings]\n저장_위치 = 원본옆\n".encode("cp949"))
check("메모장 ANSI(cp949) 로 저장한 설정도 읽음", H.load_config(cfgf)["저장_위치"] == "원본옆")
H.CONFIG_FILE = TMP / "없음.ini"
check("기본 경로는 호출할 때의 CONFIG_FILE 을 따름", H.load_config() == H.DEFAULTS)


# ── 산출물 표식 ──────────────────────────────────────────────────────
print("\n산출물 표식")
pdf_bytes = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
f = TMP / "a.pdf"
f.write_bytes(H.append_marker(pdf_bytes, dict(src="a.hwp", bytes=10, src_sha="abc", cfg="rev1")))
st, meta = H.read_marker(f)
check("붙인 그대로면 ours", st == "ours" and meta["src"] == "a.hwp", st)
check("표식은 마지막 한 줄이고 %%EOF 가 1KB 안에 남음",
      f.read_bytes().rstrip().splitlines()[-1].startswith(H.MARKER)
      and b"%%EOF" in f.read_bytes()[-1024:])
f.write_bytes(f.read_bytes() + b"1 0 obj<<>>endobj\n%%EOF\n")
check("뒤에 덧붙으면(뷰어의 증분 저장) edited", H.read_marker(f)[0] == "edited")
b = bytearray(H.append_marker(pdf_bytes, dict(src="a.hwp")))
b[12] ^= 1
f.write_bytes(bytes(b))
check("본문이 바뀌면 edited", H.read_marker(f)[0] == "edited")
f.write_bytes(pdf_bytes)
check("표식이 없으면 foreign", H.read_marker(f)[0] == "foreign")
f.write_bytes(pdf_bytes + b"\n%HWP2PDF {broken\n")
check("표식이 깨졌으면 foreign", H.read_marker(f)[0] == "foreign")
check("없는 파일은 missing", H.read_marker(TMP / "없는.pdf")[0] == "missing")


# ── 대상 정하기 ──────────────────────────────────────────────────────
print("\n대상 정하기")
A = Path("C:/x")
pairs = H.assign_outputs([(A / "d/기말.hwp", A / "o/d/기말.pdf"), (A / "d/기말.hwpx", A / "o/d/기말.pdf"),
                          (A / "e/기말.hwp", A / "o/e/기말.pdf")])
names = [o.name for _, o in pairs]
check("같은 폴더의 같은 이름 두 형식은 둘 다 형식을 붙임", names[:2] == ["기말 (hwp).pdf", "기말 (hwpx).pdf"], names)
check("다른 폴더의 같은 이름은 그대로", names[2] == "기말.pdf")
pairs = H.assign_outputs([(A / "a/시험.hwp", A / "o/시험.pdf"), (A / "b/시험.hwp", A / "o/시험.pdf")])
check("끌어다 놓은 두 파일이 같은 이름이면 번호를 붙임",
      [o.name for _, o in pairs] == ["시험 (hwp).pdf", "시험 (hwp) (2).pdf"], [o.name for _, o in pairs])

IN = TMP / "HWP 입력"
OUT = TMP / "PDF 출력"
H.IN_DIR, H.OUT_DIR = IN, OUT
for rel in ("가.hwp", "과목/나.HWPX", "과목/~$나.hwp", "과목/메모.txt", ".숨김.hwp"):
    (IN / rel).parent.mkdir(parents=True, exist_ok=True)
    (IN / rel).write_bytes(b"x")
pairs, ignored = H.plan_targets(dict(H.DEFAULTS))
got = sorted(str(o.relative_to(OUT)) for _, o in pairs)
check("하위 폴더 구조대로 출력 경로, 대소문자 확장자 인식", got == ["가.pdf", str(Path("과목/나.pdf"))], got)
check("한글 문서가 아닌 파일은 무시 목록에 (~$ 임시 파일은 아예 뺌)",
      [p.name for p in ignored] == ["메모.txt"], [p.name for p in ignored])
ext = TMP / "밖"
(ext / "묶음/하위").mkdir(parents=True)
(ext / "낱개.hwp").write_bytes(b"x")
(ext / "묶음/하위/셋.hwpx").write_bytes(b"x")
(ext / "그림.png").write_bytes(b"x")
pairs, ignored = H.plan_targets(dict(H.DEFAULTS), [ext / "낱개.hwp", ext / "묶음", ext / "그림.png"])
got = sorted(str(o.relative_to(OUT)) for _, o in pairs)
check("끌어다 놓은 파일은 출력 폴더 바로 아래, 폴더는 그 이름 아래 구조 유지",
      got == sorted(["낱개.pdf", str(Path("묶음/하위/셋.pdf"))]), got)
check("끌어다 놓은 한글 아닌 파일은 무시", [p.name for p in ignored] == ["그림.png"])
pairs, _ = H.plan_targets(dict(H.DEFAULTS, 저장_위치="원본옆"), [ext / "낱개.hwp", ext / "묶음"])
check("원본옆이면 원본 폴더에", sorted(str(o) for _, o in pairs) ==
      sorted([str(ext / "낱개.pdf"), str(ext / "묶음/하위/셋.pdf")]))

src = ext / "낱개.hwp"
out = TMP / "낱개.pdf"
meta = dict(src=src.name, bytes=src.stat().st_size, src_sha=H.file_sha16(src), cfg="rev1|rhwp0.8.7")
out.write_bytes(H.append_marker(pdf_bytes, meta))
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev1|rhwp0.8.7")
check("원본·판이 같으면 건너뜀", not todo and skipped and skipped[0][1] == "이미 변환됨")
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev2|rhwp0.8.7")
check("엔진 판이 바뀌면 다시 만듦", todo and rebuilt)
src.write_bytes(b"y")
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev1|rhwp0.8.7")
check("원본 내용이 바뀌면 다시 만듦 (크기가 같아도)", todo and rebuilt)
out.write_bytes(out.read_bytes() + b"% edited\n")
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev1|rhwp0.8.7")
check("변환 뒤 수정된 PDF 는 보존", not todo and "보존" in skipped[0][1])
out.write_bytes(pdf_bytes)
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev1|rhwp0.8.7")
check("남이 만든 같은 이름 PDF 는 막음", not todo and blocked)
out.unlink()
todo, skipped, blocked, rebuilt = H.triage([(src, out)], "rev1|rhwp0.8.7")
check("PDF 가 없으면 새로 만듦 (재생성 아님)", todo and not rebuilt)


# ── rhwp 호출 ───────────────────────────────────────────────────────
print("\nrhwp 호출")
for text, exc, words in [
        ("오류: 비밀번호가 필요한 암호 문서입니다 (--password <pw> 로 전달).", R.PasswordNeeded, "암호"),
        ("오류: 비밀번호가 일치하지 않거나 암호화 데이터가 손상되었습니다.", R.WrongPassword, "맞지 않"),
        ("오류: 문서 파싱 실패 - 유효하지 않은 파일: 지원하지 않는 포맷입니다: 알 수 없는 파일 형식. "
         "오류코드: UNSUPPORTED_FILE_FORMAT. 현재 rhwp는 …", R.RhwpError, "읽을 수 없습니다"),
        ("WARN: something\n오류: 쪽 번호가 범위를 벗어났습니다. 오류코드: X_Y.", R.RhwpError, "범위를 벗어났습니다")]:
    try:
        R._raise_for(text)
        ok, msg = False, "예외 없음"
    except exc as e:
        ok, msg = words in str(e) and "오류코드" not in str(e) and "--password" not in str(e), str(e)
    check(f"오류 문구 정리: {words}", ok, msg)
w = R.warnings_of("WARN: fallback monospace font 'D2Coding' not found.\n"
                  "      한글 또는 수식이 빈칸으로 렌더링될 수 있습니다.\n"
                  "      --fallback-mono \"<family>\" 로 설치된 폰트를 지정하세요.\n"
                  "LAYOUT_TABLE_OVERLAP: page=0, sec=0\n{\"pageCount\":2}\n")
check("늘 나오는 글꼴 안내는 빼고 조판 경고만 남김", w == ["LAYOUT_TABLE_OVERLAP: page=0, sec=0"], w)
check("2024 로 저장된 문서는 2024 조판", H.compat_for({"lastSavedWith": {"product": "hancom-office-2024"}}) == "2024")
check("그 밖은 rhwp 기본(2022)", H.compat_for({"lastSavedWith": {"product": "hancom-office-2020"}}) is None
      and H.compat_for({}) is None)
check("동시 작업 수는 1 이상, 쪽 수 이하, 8 이하", H.workers_for(1) == 1 and 1 <= H.workers_for(500) <= 8)


class FakeRhwp:
    calls = []

    def __init__(self, password=None):
        self.password = password

    def info(self, src):
        FakeRhwp.calls.append(self.password)
        if self.password is None:
            raise R.PasswordNeeded("x")
        if self.password != "맞는암호":
            raise R.WrongPassword("x")
        return {"pageCount": 1}


answers = iter(["틀린암호", "맞는암호"])
H.ask_password = lambda name, tries: next(answers)
rh, info = H.open_with_password(FakeRhwp, Path("a.hwp"), "a.hwp")
check("암호 문서는 물어서 열고, 틀리면 다시 묻는다", info == {"pageCount": 1} and FakeRhwp.calls == [None, "틀린암호", "맞는암호"])
H.ask_password = lambda name, tries: None
try:
    H.open_with_password(FakeRhwp, Path("a.hwp"), "a.hwp")
    check("Enter 만 누르면 건너뜀", False)
except R.PasswordNeeded as e:
    check("Enter 만 누르면 건너뜀", "건너뛰었습니다" in str(e), str(e))
H.ask_password = lambda name, tries: "또틀림"
try:
    H.open_with_password(FakeRhwp, Path("a.hwp"), "a.hwp")
    check("세 번 틀리면 포기", False)
except R.WrongPassword as e:
    check("세 번 틀리면 포기", "세 번" in str(e))


# ── 글자층 보정 (rhwp 모양을 흉내 낸 PDF) ─────────────────────────────
print("\n글자층 보정")


def glyph(code: int, x: float, y: float, style: int, font="/fo0", size=10.0):
    """rhwp 가 글자 하나를 그리는 모양. style 1: Tm 에 위치, style 2(굵은 글씨): cm 에 위치."""
    s = String(code.to_bytes(2, "big"))
    if style == 1:
        return [I([], Op("q")), I([], Op("q")), I([Name.cs0], Op("cs")), I([0, 0, 0], Op("scn")),
                I([0], Op("Tr")), I([], Op("q")), I([], Op("BT")), I([1, 0, 0, -1, x, y], Op("Tm")),
                I([Name(font), size], Op("Tf")), I([s], Op("Tj")), I([], Op("ET")),
                I([], Op("Q")), I([], Op("Q")), I([], Op("Q"))]
    return [I([], Op("q")), I([0.9, 0, 0, 1, x, y], Op("cm")), I([], Op("q")), I([], Op("q")),
            I([Name.cs0], Op("cs")), I([0, 0, 0], Op("scn")), I([0.3], Op("w")), I([2], Op("Tr")),
            I([], Op("q")), I([], Op("BT")), I([0.95, 0, 0, -1, 0, 0], Op("Tm")),
            I([Name(font), size], Op("Tf")), I([s], Op("Tj")), I([], Op("ET")),
            I([], Op("Q")), I([], Op("Q")), I([], Op("Q")), I([], Op("Q"))]


def fake_pdf():
    """폭 1000 인 CID 글꼴로 '가나 다' (보통), '라마  바' (굵게, 공백 둘), '사아'(글자 수 불일치) 를 그린다."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=Name.Font, Subtype=Name.Type0, BaseFont=Name("/AAAAAA+Batang"), Encoding=Name("/Identity-H"),
        DescendantFonts=[pikepdf.Dictionary(Type=Name.Font, Subtype=Name.CIDFontType2, DW=0,
                                            W=[1, 10, 1000], CIDToGIDMap=Name.Identity)]))
    H_ = 400.0
    ins = [I([], Op("q")), I([1, 0, 0, -1, 0, H_], Op("cm")),
           I([0, 0], Op("m")), I([10, 10, 300, 300], Op("re")), I([], Op("W")), I([], Op("n"))]
    ins += glyph(1, 20, 30, 1) + glyph(2, 30, 30, 1) + glyph(3, 45, 30, 1)          # 가 나 · 다
    ins += [I([20, 35], Op("m")), I([60, 35], Op("l")), I([], Op("S"))]               # 밑줄 — 경계
    ins += glyph(4, 20, 60, 2) + glyph(5, 29, 60, 2) + glyph(6, 49, 60, 2)            # 라 마 · · 바
    ins += glyph(7, 20, 90, 1) + glyph(8, 30, 90, 1)                                   # 사 아 (원문은 세 글자)
    ins += [I([], Op("Q"))]
    form = pdf.make_stream(pikepdf.unparse_content_stream(ins))
    form.Type, form.Subtype = Name.XObject, Name.Form
    form.BBox = [0, 0, 300, H_]
    form.Matrix = [1 / 300, 0, 0, 1 / H_, 0, 0]
    form.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(fo0=font),
                                        ColorSpace=pikepdf.Dictionary(cs0=Name.DeviceRGB))
    page = pikepdf.Page(pikepdf.Dictionary(Type=Name.Page, MediaBox=[0, 0, 225, 300]))
    pdf.pages.append(page)
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(S1=form))
    page.Contents = pdf.make_stream(b"225 0 0 300 0 0 cm /S1 Do")
    tree = {"type": "Page", "children": [
        {"type": "TextRun", "text": "가나 다", "bbox": {"x": 20, "y": 20, "w": 35, "h": 12}},
        {"type": "TextRun", "text": " 라마  바 ", "bbox": {"x": 20, "y": 50, "w": 39, "h": 12}},
        {"type": "TextRun", "text": "사아자", "bbox": {"x": 20, "y": 80, "w": 30, "h": 12}}]}
    return pdf, tree, ins


pdf, tree, orig = fake_pdf()
form = P.page_form(pdf.pages[0])[1]
form_matrix = [str(x) for x in form.Matrix]
gl = [g for g in P.find_glyphs(list(pikepdf.parse_content_stream(form)))]
check("글자 묶음 8개를 찾음 (밑줄·클립은 글자가 아님)", len(gl) == 8, len(gl))
fins = list(pikepdf.parse_content_stream(form))
ok = [P.measure(fins, g) for g in gl]
check("보통·굵은 글씨 모두 위치를 잼", all(ok) and [round(g.x) for g in gl[3:6]] == [20, 29, 49],
      [(round(g.x, 1), round(g.y, 1), round(g.a, 3)) for g in gl])
st = P.fix_page(pdf, pdf.pages[0], tree)
check("쪽을 감싼 Form 을 펼침", st["flattened"] and P.page_form(pdf.pages[0]) is None)
new = list(pikepdf.parse_content_stream(pdf.pages[0]))
head = [str(i.operator) for i in new[:6]]
check("머리는 q·쪽 행렬·Form 행렬·BBox 클립", head == ["q", "cm", "cm", "re", "W", "n"], head)
check("Form 의 /Matrix 를 반올림 없이 그대로 씀", [str(x) for x in new[2].operands] == form_matrix,
      [str(x) for x in new[2].operands])
check("글꼴 자원이 쪽으로 옮겨지고 공백 글꼴이 더해짐",
      set(map(str, pdf.pages[0].Resources.Font.keys())) == {"/fo0", P.SPACE_FONT})
depth = 0
bal = True
for i in new:
    depth += {"q": 1, "Q": -1}.get(str(i.operator), 0)
    bal &= depth >= 0
check("q/Q 짝이 맞음", bal and depth == 0, depth)
shown = []
for k, i in enumerate(new):
    if str(i.operator) == "Tj":
        f = next(str(j.operands[0]) for j in reversed(new[:k]) if str(j.operator) == "Tf")
        shown.append(bytes(i.operands[0]).decode("latin1") if f == P.SPACE_FONT
                     else "가나다라마바사아"[int.from_bytes(bytes(i.operands[0]), "big") - 1])
check("원문 공백이 제자리에 들어감 (앞 공백·두 칸·끝 공백 포함)",
      "".join(shown) == "가나 다 라마  바 사아", repr("".join(shown)))
check("글자 수가 원문과 다른 묶음은 공백을 넣지 않음", st["matched"] == 2 and st["runs"] == 3, st)
orig_ops = [(str(i.operator), [str(o) for o in i.operands]) for i in orig]
sp_free = []
k = 0
seq = new[6:-1]
while k < len(seq):
    if (str(seq[k].operator) == "q" and k + 2 < len(seq) and str(seq[k + 1].operator) == "BT"
            and str(seq[k + 2].operator) == "Tf" and str(seq[k + 2].operands[0]) == P.SPACE_FONT):
        k += 8
        continue
    sp_free.append((str(seq[k].operator), [str(o) for o in seq[k].operands]))
    k += 1
check("공백을 빼면 원래 그리기 명령이 순서·값 그대로", sp_free == orig_ops)
g = [g for g in P.find_glyphs(fins) if P.measure(fins, g)][0]
ops = P._space_ops(g, " ", {"/fo0": ({1: 1000}, 0)}, "after")
check("공백은 q…Q 로 감싸 Tr 3 이 뒤로 새지 않음",
      [str(i.operator) for i in ops] == ["q", "BT", "Tf", "Tr", "Tm", "Tj", "ET", "Q"]
      and ops[3].operands[0] == 3)
check("공백은 앞 글자 폭만큼 뒤에", abs(float(ops[4].operands[4]) - (20 + 10)) < 1e-6, ops[4].operands)
other = pikepdf.new()
other.pages.append(pikepdf.Page(pikepdf.Dictionary(Type=Name.Page, MediaBox=[0, 0, 100, 100])))
other.pages[0].Contents = other.make_stream(b"BT /F1 12 Tf (x) Tj ET")
st2 = P.fix_page(other, other.pages[0], None)
check("모양이 다른 쪽은 손대지 않음", not st2["flattened"] and "skip" in st2)
fw = P.font_widths(pikepdf.Dictionary(DescendantFonts=[pikepdf.Dictionary(DW=500, W=[3, [600, 700], 10, 12, 900])]))
check("/W 두 형식을 모두 읽음", fw == ({3: 600.0, 4: 700.0, 10: 900.0, 11: 900.0, 12: 900.0}, 500.0), fw)


# ── 스스로 재기 ─────────────────────────────────────────────────────
print("\n스스로 재기")
rc, rw = fidelity.text_recall("가나다 라마바 abc", "가나다 라마 바 xyz")
check("한글 글자·낱말 회수율", rc == 1.0 and abs(rw - 0.5) < 1e-9, (rc, rw))
check("한글이 없으면 None", fidelity.text_recall("abc def", "abc") == (None, None))

from PIL import Image, ImageDraw  # noqa: E402


def page_image(lines, shift=0, size=(724, 1024)):
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for (x0, y0, x1) in lines:
        d.rectangle([x0 + shift, y0 + shift, x1 + shift, y0 + 9 + shift], fill="black")
    return im


layout = [(60, y, 660 - (y * 7) % 200) for y in range(80, 900, 26)]
img = page_image(layout)
pdf_path = TMP / "p.pdf"
img.save(pdf_path, "PDF", resolution=72 * 724 / 595)
for name, im, cond in [("같은 그림", img, lambda s: s is not None and s > 0.99),
                       ("3화소 밀린 그림", page_image(layout, 3), lambda s: s is not None and s > 0.95),
                       ("단이 무너진 그림", page_image([(380, y, 680) for y in range(80, 900, 52)]),
                        lambda s: s is not None and s < H.PREVIEW_WARN),
                       ("빈 미리보기", Image.new("RGB", (724, 1024), "white"), lambda s: s is None),
                       ("가로세로 비가 다른 그림", Image.new("RGB", (1024, 724), "black"), lambda s: s is None)]:
    pv = TMP / "pv.png"
    im.save(pv)
    s = fidelity.preview_match(pdf_path, pv)
    check(f"미리보기 대조: {name}", cond(s), s)

line, notes = H.summarize(dict(pages=3, seconds=1.2, preview=0.6, hangul=0.9, warns=["LAYOUT_X: a"]))
check("낮은 점수는 확인 권장 문구로", len(notes) == 2 and "한컴 미리보기" in notes[0] and "90%" in notes[1], notes)
check("rhwp 조판 경고는 확인 권장이 아니라 요약 줄에", "rhwp 경고 LAYOUT_X" in line, line)
line, notes = H.summarize(dict(pages=1, seconds=0.4, preview=None, hangul=None, warns=[]))
check("잴 수 없으면 문구 없이", notes == [] and line == "1쪽 · 0.4초", line)

print(f"\n결과: {'전부 통과' if not fails else '실패 ' + ', '.join(fails)} ({passed}/{passed + len(fails)})")
sys.exit(1 if fails else 0)
