# -*- coding: utf-8 -*-
"""종단 검증 — 합성 한글 문서를 실제로 변환해 결과를 채점한다.

    engine\\venv\\Scripts\\python engine\\종단_검증.py           전체 (약 1분)
    engine\\venv\\Scripts\\python engine\\종단_검증.py --smoke   변환 한 건만 (환경 설치 확인용)

사용자 문서는 쓰지 않는다. 시험 문서는 rhwp scaffold 로 그때그때 만든다(제목·개요·문단·표).
도구 폴더의 "HWP 입력"·"PDF 출력"·실행기록은 건드리지 않는다 — 모든 경로를 임시 폴더로 돌린다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import pikepdf  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
import numpy as np  # noqa: E402

import fidelity  # noqa: E402
import hwp2pdf  # noqa: E402
from rhwp_cli import RHWP_EXE, Rhwp  # noqa: E402

RESULTS: list[tuple[bool, str]] = []

# 같은 음절이 다섯 글자 안에 되풀이되는 문장 — rhwp 원본 PDF 에서 pdfium 이 글자를 버리는 조건
BODY = ("이 문서는 대학물리 학기말 시험의 안내문입니다. 시험 시간은 오후 여섯 시이며 "
        "학번과 이름을 반드시 적습니다. 답을 적은 뒤에는 답지를 다시 확인하시오.")


def check(ok, msg: str):
    RESULTS.append((bool(ok), msg))
    print(f"  {'✔' if ok else '✘'} {msg}", flush=True)
    return ok


def scaffold(path: Path, title: str, paragraphs: int = 6, table: bool = True, salt: str = ""):
    blocks = [{"type": "heading", "level": 1, "text": f"1. {title} 안내{salt}"}]
    for k in range(paragraphs):
        blocks.append({"type": "paragraph", "text": f"{k + 1}. {BODY}{salt}"})
        if table and k == 1:
            blocks.append({"type": "table", "rows": [["문제", "1", "2", "3"], ["답", "가", "나", "다"]]})
    spec = {"version": "1", "title": title, "blocks": blocks}
    path.parent.mkdir(parents=True, exist_ok=True)
    sp = path.with_suffix(".json")
    sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([str(RHWP_EXE), "scaffold", str(sp), "-o", str(path)], capture_output=True)
    sp.unlink()
    if r.returncode != 0 or not path.is_file():
        raise RuntimeError("rhwp scaffold 실패: " + r.stderr.decode("utf-8", "replace")[-300:])
    return path


def make_bold(path: Path) -> Path:
    """본문 글자 모양을 굵게 바꾼다.

    rhwp 는 굵은 글자를 '채움+윤곽선(Tr 2)' 으로 그리며 위치를 cm 으로 주고 글자 행렬은 모두
    원점에 둔다. Form 안에서는 pdfium 이 이것을 '같은 자리의 같은 글자(가짜 굵게)' 로 보고
    앞의 다섯 글자 안에 되풀이된 글자를 버린다 — 실제 시험지에서 탈락한 글자가 전부 굵은 글씨였다.
    """
    import re
    tmp = path.with_suffix(".bold")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "Contents/header.xml":
                data = re.sub(rb'(<hh:charPr id="0"[^>]*>)', rb"\1<hh:bold/>", data)
            zout.writestr(it, data)
    tmp.replace(path)
    return path


def to_hwp5(src: Path, dst: Path):
    r = subprocess.run([str(RHWP_EXE), "convert", str(src), str(dst)], capture_output=True)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError("rhwp convert 실패: " + r.stderr.decode("utf-8", "replace")[-300:])
    return dst


def pdf_text(path: Path) -> str:
    d = pdfium.PdfDocument(str(path))
    try:
        return "\n".join(d[i].get_textpage().get_text_range() for i in range(len(d)))
    finally:
        d.close()


def page_count(path: Path) -> int:
    with pikepdf.open(path) as p:
        return len(p.pages)


def render(path: Path, i: int = 0, dpi: int = 100):
    d = pdfium.PdfDocument(str(path))
    try:
        return np.asarray(d[i].render(scale=dpi / 72).to_pil().convert("L"), dtype=np.int16)
    finally:
        d.close()


def inserted_spaces(path: Path) -> int:
    """우리가 끼워 넣은 보이지 않는 공백 묶음의 수 (내용 흐름에서 공백 전용 글꼴을 고른 횟수).

    pdfium 은 이 공백을 자기가 간격으로 추정한 공백과 합쳐 '생성된 공백' 으로 돌려주므로
    추출 결과로는 셀 수 없다 — 흐름을 직접 센다.
    """
    import pdf_text
    with pikepdf.open(path) as p:
        return sum(1 for pg in p.pages for i in pikepdf.parse_content_stream(pg)
                   if str(i.operator) == "Tf" and str(i.operands[0]) == pdf_text.SPACE_FONT)


def cross_extract(path: Path) -> dict:
    """다른 추출기(pypdf·pdfminer)가 깔려 있으면 그것으로도 뽑아 본다 — CI 에서만 설치한다."""
    out = {}
    try:
        import pypdf
        out["pypdf"] = "\n".join(pg.extract_text() for pg in pypdf.PdfReader(str(path)).pages)
    except ImportError:
        pass
    try:
        import logging
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        from pdfminer.high_level import extract_text
        out["pdfminer"] = extract_text(str(path))
    except ImportError:
        pass
    return out


def same_picture(a, b) -> tuple[bool, str]:
    """눈에 보이는 차이가 없는가 — 어느 화소도 회색조 4단계(256단계 중)를 넘게 바뀌지 않았다.

    Form 을 펼치면 pdfium 의 행렬 곱 순서가 달라져 글자 가장자리 화소가 1~2단계 흔들린다
    (실측: 보통 글씨 0.01%, 윤곽선을 그리는 굵은 글씨 0.2% 화소). 글자가 한 화소라도
    움직이거나 빠지면 가장자리에서 100단계 넘게 달라지므로 4단계로 충분히 가려진다.
    """
    d = np.abs(a - b)
    frac = float((d > 0).mean())
    return bool(d.max() <= 4), f"최대 {int(d.max())}단계, {frac * 100:.3f}% 화소"


class Sandbox:
    """hwp2pdf 의 경로를 임시 폴더로 돌린다."""

    def __init__(self, root: Path):
        self.root = root
        self.saved = {}
        self.revealed = []

    def __enter__(self):
        h = hwp2pdf
        for k in ("BASE", "IN_DIR", "OUT_DIR", "CONFIG_FILE", "LOG_FILE", "TMP_ROOT"):
            self.saved[k] = getattr(h, k)
        self.saved["acquire_lock"] = h.acquire_lock
        self.saved["reveal"] = h.reveal
        h.BASE = self.root
        h.IN_DIR = self.root / "HWP 입력"
        h.OUT_DIR = self.root / "PDF 출력"
        h.CONFIG_FILE = self.root / "설정.ini"
        h.LOG_FILE = self.root / "실행기록.txt"
        h.TMP_ROOT = self.root / ".tmp"
        h.acquire_lock = lambda: True
        h.reveal = lambda outs: self.revealed.append(list(outs))
        h.IN_DIR.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(hwp2pdf, k, v)

    def config(self, **kv):
        lines = ["[settings]"] + [f"{k} = {v}" for k, v in kv.items()]
        hwp2pdf.CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")


def run_main(args=()):
    t = time.monotonic()
    rc = hwp2pdf.main(list(map(str, args)))
    return rc, time.monotonic() - t


def smoke() -> int:
    """환경 설치 직후의 확인 — 합성 문서 하나를 끝까지 변환한다."""
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_smoke_") as td:
        td = Path(td)
        src = scaffold(td / "시험.hwpx", "환경 확인", paragraphs=3)
        out = td / "시험.pdf"
        ver = Rhwp().version()
        res = hwp2pdf.convert_one(src, out, dict(hwp2pdf.DEFAULTS), ver, td / "job", src.name)
        hc = res.get("hangul") or 0
        ok = out.is_file() and res["pages"] >= 1 and hc >= 0.99
        print(f"합성 문서 변환 {'성공' if ok else '실패'} ({res['pages']}쪽, 글자층 {hc * 100:.0f}%, {res['seconds']:.1f}초)")
        return 0 if ok else 1


def full() -> int:
    ver = Rhwp().version()
    print(f"rhwp {ver} · 종단 검증 시작")
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_e2e_") as td, Sandbox(Path(td) / "tool") as sb:
        td = Path(td)
        IN, OUT = hwp2pdf.IN_DIR, hwp2pdf.OUT_DIR

        print("\n[1] 폴더 변환 — 여러 쪽 문서·하위 폴더·같은 이름 두 형식·깨진 파일·다른 형식")
        a = make_bold(scaffold(IN / "시험 안내.hwpx", "대학물리 시험", paragraphs=40))
        b = scaffold(IN / "과목" / "기출.hwpx", "전기회로 기출", paragraphs=3)
        to_hwp5(scaffold(td / "tmp_c.hwpx", "같은 이름", paragraphs=2), IN / "과목" / "기출.hwp")
        (IN / "깨진.hwp").write_bytes(b"this is not a hwp file" * 10)
        (IN / "메모.txt").write_text("무시되어야 함", encoding="utf-8")
        rc, dt = run_main()
        pa, pb, pc = OUT / "시험 안내.pdf", OUT / "과목" / "기출 (hwpx).pdf", OUT / "과목" / "기출 (hwp).pdf"
        check(rc == 1, f"깨진 파일이 있으면 종료 코드 1 (실제 {rc}, {dt:.1f}초)")
        check(pa.is_file() and pb.is_file() and pc.is_file(), "정상 문서 3개가 모두 변환됨 (하위 폴더 구조 유지)")
        check(not (OUT / "깨진.pdf").exists(), "깨진 파일은 PDF 를 남기지 않음")
        check(not list(OUT.rglob("*.tmp")), "반쪽 임시 파일이 남지 않음")
        log = hwp2pdf.LOG_FILE.read_text(encoding="utf-8")
        check("깨진.hwp" in log and "HWP 문서로 읽을 수 없습니다" in log, "실패 사유가 실행기록에 한국어로 남음")
        check(sb.revealed and len(sb.revealed[-1]) == 3, "끝나면 결과를 탐색기로 보여 줌")

        n_a = Rhwp().info(a)["pageCount"]
        check(n_a >= 3 and page_count(pa) == n_a, f"쪽 수가 문서와 같음 ({n_a}쪽 — 쪽별 동시 내보내기 경로)")
        meta_state, meta = hwp2pdf.read_marker(pa)
        check(meta_state == "ours" and meta.get("src") == a.name, "산출물 표식이 붙고 원본을 가리킴")
        with pikepdf.open(pa) as p:
            check(str(p.docinfo.get("/Title")) == "시험 안내", "PDF 제목이 원본 파일 이름")

        print("\n[2] 글자층 — rhwp 원본 PDF 와 비교")
        raw = td / "raw.pdf"
        Rhwp().export_pdf(a, raw)
        ref = "\n".join(Rhwp().export_text(a, td / "ref"))
        rc0, rw0 = fidelity.text_recall(ref, pdf_text(raw))
        rc1, rw1 = fidelity.text_recall(ref, pdf_text(pa))
        print(f"    rhwp 원본: 한글 글자 {rc0:.3f} · 낱말 {rw0:.3f}  →  보정 후: 글자 {rc1:.3f} · 낱말 {rw1:.3f}")
        check(rc1 >= 0.995, f"보정 후 pdfium 이 한글 글자를 다 뽑음 ({rc1:.3f})")
        check(rw1 >= 0.98, f"보정 후 한글 낱말(띄어쓰기 기준)이 살아 있음 ({rw1:.3f})")
        if rc0 < 0.99:
            check(rc1 > rc0, f"원본의 굵은 글씨 탈락({rc0:.3f})을 보정이 메움")
        else:
            print("    (참고) 이 rhwp 판의 원본 PDF 는 굵은 글씨도 빠지지 않습니다 — 상류에서 고쳐졌을 수 있음")
        sp = inserted_spaces(pa)
        check(sp >= BODY.count(" ") * 40, f"띄어쓰기 자리에 보이지 않는 공백이 들어감 ({sp}곳)")
        raw_x = cross_extract(raw)
        for name, txt in cross_extract(pa).items():
            w0 = fidelity.text_recall(ref, raw_x[name])[1] or 0
            w1 = fidelity.text_recall(ref, txt)[1] or 0
            check(w1 >= 0.95, f"{name} 로 뽑아도 한글 낱말이 살아 있음 (원본 {w0:.3f} → {w1:.3f})")
        res = [same_picture(render(raw, i), render(pa, i)) for i in range(n_a)]
        check(all(r for r, _ in res), "보정이 그림을 바꾸지 않음 (" + " / ".join(m for _, m in res) + ")")
        from rhwp_cli import export_pdf_pages
        files, _ = export_pdf_pages(Rhwp(), a, td / "parts", n_a, 4)
        d3 = [int(np.abs(render(files[i]) - render(raw, i)).max()) for i in range(n_a)]
        check(max(d3) == 0, f"쪽별 동시 내보내기가 한 번에 내보낸 것과 같은 그림 (최대 화소 차 {max(d3)})")

        # 같은 원본·같은 엔진이면 같은 바이트 (끝의 표식 줄은 만든 시각이 들어가 뺀다)
        twice = []
        for k in range(2):
            o = td / f"twice{k}.pdf"
            hwp2pdf.convert_one(a, o, dict(hwp2pdf.DEFAULTS), ver, td / f"twice_job{k}", a.name)
            data = o.read_bytes()
            twice.append(data[:data.rfind(hwp2pdf.MARKER)])
        check(twice[0] == twice[1], "같은 원본을 두 번 변환하면 PDF 가 바이트로 같음 (표식 줄 제외)")

        print("\n[3] 다시 실행 — 건너뛰기·재변환·보존")
        mt = {p: p.stat().st_mtime_ns for p in (pa, pb, pc)}
        (IN / "깨진.hwp").unlink()
        rc, dt = run_main()
        check(rc == 0 and all(p.stat().st_mtime_ns == mt[p] for p in mt), f"바뀐 것이 없으면 모두 건너뜀 ({dt:.1f}초)")
        scaffold(b, "전기회로 기출", paragraphs=3, salt=" (수정)")
        rc, _ = run_main()
        check(pb.stat().st_mtime_ns != mt[pb] and pa.stat().st_mtime_ns == mt[pa],
              "원본이 바뀐 문서만 다시 만듦")
        check("(수정)" in pdf_text(pb), "다시 만든 PDF 에 바뀐 내용이 들어감")
        with pa.open("ab") as fh:          # 뷰어가 주석을 덧붙여 저장한 상황
            fh.write(b"\n% annotation saved by a viewer\n")
        make_bold(scaffold(a, "대학물리 시험", paragraphs=40, salt=" (개정)"))
        before = pa.read_bytes()
        rc, _ = run_main()
        check(pa.read_bytes() == before, "변환 뒤 수정된 PDF 는 원본이 바뀌어도 덮어쓰지 않음")
        check(hwp2pdf.read_marker(pa)[0] == "edited", "수정된 PDF 를 '수정됨' 으로 판정")
        foreign = OUT / "남의 것.pdf"
        foreign.write_bytes(raw.read_bytes())
        scaffold(IN / "남의 것.hwpx", "이름 충돌", paragraphs=2)
        rc, _ = run_main()
        check(foreign.read_bytes() == raw.read_bytes(), "이 도구가 만들지 않은 같은 이름 PDF 는 덮어쓰지 않음")

        print("\n[4] 끌어다 놓기 · 원본 옆 저장")
        ext = td / "바탕화면"
        d1 = scaffold(ext / "낱개 문서.hwpx", "끌어다 놓기", paragraphs=2)
        folder = ext / "기출 묶음"
        scaffold(folder / "1회.hwpx", "묶음 하나", paragraphs=2)
        scaffold(folder / "하위" / "2회.hwpx", "묶음 둘", paragraphs=2)
        rc, _ = run_main([d1, folder])
        check(rc == 0 and (OUT / "낱개 문서.pdf").is_file(), "끌어다 놓은 파일이 출력 폴더로 변환됨")
        check((OUT / "기출 묶음" / "1회.pdf").is_file() and (OUT / "기출 묶음" / "하위" / "2회.pdf").is_file(),
              "끌어다 놓은 폴더는 구조를 유지해 변환됨")
        check(not (IN / "낱개 문서.hwpx").exists(), "끌어다 놓기는 입력 폴더를 건드리지 않음")
        sb.config(저장_위치="원본옆")
        d2 = scaffold(ext / "옆에 저장.hwpx", "원본 옆", paragraphs=2)
        rc, _ = run_main([d2])
        check((ext / "옆에 저장.pdf").is_file(), "저장_위치 = 원본옆 이면 원본과 같은 폴더에 저장")
        sb.config()

        print("\n[5] 한컴 미리보기 대조")
        good = td / "미리보기 일치.hwpx"
        bad = td / "미리보기 불일치.hwpx"
        src = scaffold(td / "pv_src.hwpx", "미리보기 시험", paragraphs=12)
        pv_pdf = td / "pv.pdf"
        Rhwp().export_pdf(src, pv_pdf)
        doc = pdfium.PdfDocument(str(pv_pdf))
        w, h = doc[0].get_size()
        img = doc[0].render(scale=724 / w).to_pil().convert("RGB")
        doc.close()
        from PIL import Image, ImageDraw
        wrong = Image.new("RGB", img.size, "white")
        dr = ImageDraw.Draw(wrong)
        for y in range(80, img.size[1] - 80, 60):        # 단이 무너진 모양 흉내 — 엉뚱한 자리의 글줄
            dr.rectangle([400, y, 680, y + 14], fill="black")
        for path, im in ((good, img), (bad, wrong)):
            with zipfile.ZipFile(src) as zin, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
                for it in zin.infolist():
                    if not it.filename.startswith("Preview/PrvImage"):
                        zout.writestr(it, zin.read(it.filename))
                import io
                bio = io.BytesIO()
                im.save(bio, "PNG")
                zout.writestr("Preview/PrvImage.png", bio.getvalue())
        cfg = dict(hwp2pdf.DEFAULTS)
        rg = hwp2pdf.convert_one(good, td / "g.pdf", cfg, ver, td / "jg", good.name)
        rb = hwp2pdf.convert_one(bad, td / "b.pdf", cfg, ver, td / "jb", bad.name)
        check(rg.get("preview") is not None and rg["preview"] >= 0.95, f"같은 모양의 미리보기는 높은 점수 ({rg.get('preview')})")
        check(rb.get("preview") is not None and rb["preview"] < hwp2pdf.PREVIEW_WARN,
              f"다른 모양의 미리보기는 경고 문턱 아래 ({rb.get('preview')})")
        _, notes = hwp2pdf.summarize(rb)
        check(any("한컴 미리보기" in n for n in notes), "경고 문구가 요약에 들어감")
        rn = hwp2pdf.convert_one(src, td / "n.pdf", cfg, ver, td / "jn", src.name)
        check(rn.get("preview") is None, "미리보기가 없는 문서는 대조하지 않음")

    ok = sum(1 for r, _ in RESULTS if r)
    print(f"\n종단 검증: {ok}/{len(RESULTS)} 통과")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    if not RHWP_EXE.is_file():
        print(f"rhwp 가 없습니다: {RHWP_EXE} — 환경 설치.bat 을 먼저 실행하세요")
        sys.exit(2)
    sys.exit(smoke() if "--smoke" in sys.argv else full())
