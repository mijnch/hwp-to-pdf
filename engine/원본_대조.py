# -*- coding: utf-8 -*-
"""변환 결과를 원본(한컴이 그린 그림)과 맞대 일치율을 잰다 — 엔진을 고치거나 판을 올릴 때의 잣대.

    engine\\venv\\Scripts\\python.exe engine\\원본_대조.py [파일·폴더 ...]
        한글 문서마다 1쪽을 그려 문서 안의 한컴 미리보기와 맞댄다. 한컴이 없어도 된다.
        대상을 주지 않으면 'HWP 입력' 폴더.
    engine\\venv\\Scripts\\python.exe engine\\원본_대조.py --쌍 <폴더>
        폴더 안의 같은 이름 쌍(문서.hwp|.hwpx + 한컴오피스가 만든 문서.pdf)을 모든 쪽 96dpi 로 맞댄다.
    engine\\venv\\Scripts\\python.exe engine\\원본_대조.py --rhwp-시험
        rhwp 저장소의 한컴 PDF 34쌍(engine\\원본대조_목록.json — 커밋 고정, git 지문 대조)을
        원본대조\\rhwp 에 받아 --쌍 과 같이 잰다(약 38MB, 처음 한 번).

    engine\\venv\\Scripts\\python.exe engine\\원본_대조.py --기준 [추가 <파일...> | 갱신]
        기준선 — 등록한 문서를 실제 도구 경로로 변환해 쪽 수·글자층·1쪽 실루엣·칸 정렬을 재고
        원본대조\\기준.json 의 앞 점수와 비교한다. 나빠진 문서가 있으면 종료 코드 1.
        (PDF OCR 의 정답지 채점과 같은 역할. 문서는 복사하지 않고 경로·지문·점수만 적는다.)

    --차이        90% 미만 쪽의 겹친 그림을 원본대조\\차이 에 남긴다 (빨강 = 우리만, 파랑 = 원본만)
    --json 파일   결과를 JSON 으로 남긴다 (판끼리 비교용)

지표
  실루엣 일치율(%) — rhwp 개발진의 Visual Sweep 과 같은 식(engine\\fidelity.py silhouette).
  쪽마다 잰다. 미리보기 대조에서는 미리보기 해상도(약 70~90dpi)에서 재고, 한컴 미리보기의
  세로 눌림(0.58%)을 맞춘다. 칸 단위 정렬 점수(실행기록의 '한컴 미리보기 일치')도 같이 적는다.

변환은 실제 도구와 같은 경로(hwp2pdf.convert_one)로 한다. 결과는 임시 폴더에만 쓰고 지운다.
'원본대조\\' 폴더는 .gitignore 대상이다 — 사용자 문서·남의 문서를 저장소에 넣지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE_DIR))

import hwp2pdf  # noqa: E402



def _no_prompt():
    """재기만 하는 도구다 — 암호 문서는 묻지 않고 건너뛴다(작업 프로세스에서 묻으면 입력을 기다리며 멈춘다).
    모듈을 불러오기만 하는 쪽(단위 검증)에 영향이 없도록 main 과 작업 프로세스 시작에서만 건다."""
    hwp2pdf.ask_password = lambda name, tries: None

WORK = hwp2pdf.BASE / "원본대조"
LIST = ENGINE_DIR / "원본대조_목록.json"
DPI = 96
GOOD = 90.0          # 쪽 하나가 '맞았다' 고 볼 실루엣 일치율 (rhwp Visual Sweep 의 문턱)
# 이보다 작은 미리보기(옛 한컴의 177×250 등)에서는 실루엣을 재지 않는다. 우리 글자는 그 크기에서
# 흐린 회색이라 내용 화소(RGB<232)로 잡히지 않는데 한컴 미리보기 글자는 진한 검정이라,
# 배치가 눈에 띄게 달라도 99.9% 가 나왔다(공개 문서 실측). 칸 정렬 점수는 그대로 잰다.
SIL_MIN_PX = 400


# ── 공통 ────────────────────────────────────────────────────────────

def convert(src: Path, td: Path) -> Path:
    """실제 도구와 같은 경로로 문서 전체를 변환한다."""
    from rhwp_cli import Rhwp
    out = td / "out.pdf"
    cfg = dict(hwp2pdf.DEFAULTS, 미리보기_대조=False)
    hwp2pdf.convert_one(src, out, cfg, Rhwp().version(), td / "job", src.name)
    return out


def render(pdf_path: Path, dpi: int = DPI) -> list:
    import pypdfium2 as pdfium
    d = pdfium.PdfDocument(str(pdf_path))
    try:
        return [d[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(d))]
    finally:
        d.close()


def overlay(a, b, path: Path) -> None:
    """빨강 = 우리에게만 있는 내용, 파랑 = 원본에만 있는 내용, 회색 = 함께 있는 내용 (2px 관용)."""
    import numpy as np
    from PIL import Image, ImageFilter
    W, H = max(a.width, b.width), max(a.height, b.height)

    def mask(im):
        x = Image.new("RGB", (W, H), "white")
        x.paste(im)
        return np.asarray(x).min(axis=2) < 232

    am, bm = mask(a), mask(b)
    near = lambda m: np.asarray(Image.fromarray(m.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0  # noqa: E731
    an, bn = near(am), near(bm)
    out = np.full((H, W, 3), 255, np.uint8)
    out[am & bn] = (170, 170, 170)
    out[bm & an & ~am] = (170, 170, 170)
    out[am & ~bn] = (220, 30, 30)
    out[bm & ~an] = (30, 60, 220)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(path)


# ── 미리보기 대조 ───────────────────────────────────────────────────

def preview_one(src: Path) -> dict:
    """1쪽만 그려 미리보기와 맞댄다. 도구는 쪽마다 따로 그려 합치므로(종단 검증이 '쪽별 = 한 번에'를
    화소로 확인) 1쪽만 그린 것이 실제 산출물의 1쪽과 같다 — 글자층 보정은 그림을 바꾸지 않는다."""
    import fidelity
    from rhwp_cli import Rhwp, RhwpError
    rec = dict(name=src.name)
    t = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_cmp_", dir=hwp2pdf.TMP_ROOT, ignore_cleanup_errors=True) as td:
        td = Path(td)
        thumb = td / "thumb.png"
        if not Rhwp().thumbnail(src, thumb):
            rec["err"] = "한컴 미리보기 없음 (한컴오피스가 저장한 문서가 아님)"
            return rec
        try:
            rh, info = hwp2pdf.open_with_password(Rhwp, src, src.name)
            out = td / "p1.pdf"
            rh.export_pdf(src, out, page=0, compat=hwp2pdf.compat_for(info))
        except (RhwpError, RuntimeError, OSError) as e:
            rec["err"] = str(e)[:160]
            return rec
        rec["pages"] = int(info.get("pageCount") or 0)
        rec["match"] = fidelity.preview_match(out, thumb)
        from PIL import Image
        with Image.open(thumb) as im:
            rec["preview_px"] = list(im.size)
        if min(im.size) >= SIL_MIN_PX:
            rec["silhouette"] = fidelity.preview_silhouette(out, thumb)
    rec["sec"] = round(time.monotonic() - t, 1)
    return rec


def run_preview(targets: list[Path]) -> list[dict]:
    files = []
    for t in targets:
        if t.is_dir():
            files += sorted(p for p in t.rglob("*") if p.suffix.lower() in hwp2pdf.HWP_EXTS and p.is_file())
        elif t.suffix.lower() in hwp2pdf.HWP_EXTS:
            files.append(t)
    if not files:
        print("맞댈 한글 문서가 없습니다.")
        return []
    print(f"한글 문서 {len(files)}개 — 1쪽을 한컴 미리보기와 맞댑니다\n")
    recs = []
    with ProcessPoolExecutor(min(4, len(files)), initializer=_no_prompt) as ex:
        for fut in as_completed([ex.submit(preview_one, f) for f in files]):
            rec = fut.result()
            recs.append(rec)
            if "err" in rec:
                print(f"  -  {rec['name']}: {rec['err']}")
            else:
                m = f"{rec['match']:.2f}" if rec.get("match") is not None else "-"
                s = f"{rec['silhouette']:5.1f}%" if rec.get("silhouette") is not None else "    -"
                print(f"  {s}  (칸 정렬 {m})  {rec['name']}")
    recs.sort(key=lambda r: r["name"])
    sils = [r["silhouette"] for r in recs if r.get("silhouette") is not None]
    if sils:
        print(f"\n실루엣 일치율 평균 {sum(sils) / len(sils):.2f}% · 문서 {len(sils)}개"
              f" · {GOOD:.0f}% 이상 {sum(s >= GOOD for s in sils)}개")
    matches = sorted(r["match"] for r in recs if r.get("match") is not None)
    if matches:
        print(f"칸 정렬 점수 중앙값 {matches[len(matches) // 2]:.3f} · 문서 {len(matches)}개"
              f" · 확인 권장 문턱({hwp2pdf.PREVIEW_WARN}) 미만 {sum(m < hwp2pdf.PREVIEW_WARN for m in matches)}개")
    return recs


# ── 한컴 PDF 쌍 대조 ────────────────────────────────────────────────

def pair_one(args) -> dict:
    import fidelity
    from rhwp_cli import RhwpError
    doc, ref, diff_dir = args
    rec = dict(name=doc.name, ref=ref.name)
    t = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_cmp_", dir=hwp2pdf.TMP_ROOT, ignore_cleanup_errors=True) as td:
        td = Path(td)
        try:
            out = convert(doc, td)
            A, B = render(out), render(ref)
        except (RhwpError, RuntimeError, OSError) as e:
            rec["err"] = str(e)[:160]
            return rec
    rec["pages"], rec["ref_pages"] = len(A), len(B)
    rec["scores"] = [round(fidelity.silhouette(a, b), 2) for a, b in zip(A, B)]
    if diff_dir:
        for i, (s, a, b) in enumerate(zip(rec["scores"], A, B), 1):
            if s < GOOD:
                overlay(a, b, diff_dir / f"{doc.stem}_{i:03d}쪽_{s:.0f}.png")
    rec["sec"] = round(time.monotonic() - t, 1)
    return rec


def find_pairs(folder: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for doc in sorted(folder.iterdir()):
        if doc.suffix.lower() in hwp2pdf.HWP_EXTS:
            ref = doc.with_suffix(".pdf")
            if ref.is_file():
                pairs.append((doc, ref))
    return pairs


def run_pairs(pairs: list[tuple[Path, Path]], diff: bool) -> list[dict]:
    import numpy as np
    if not pairs:
        print("같은 이름의 (한글 문서, PDF) 쌍이 없습니다.")
        return []
    diff_dir = WORK / "차이" if diff else None
    print(f"쌍 {len(pairs)}개 — 모든 쪽을 {DPI}dpi 로 맞댑니다\n")
    recs = []
    with ProcessPoolExecutor(min(4, len(pairs)), initializer=_no_prompt) as ex:
        for fut in as_completed([ex.submit(pair_one, (d, r, diff_dir)) for d, r in pairs]):
            rec = fut.result()
            recs.append(rec)
            if "err" in rec:
                print(f"  -  {rec['name']}: {rec['err']}")
                continue
            sc = rec["scores"]
            pc = "" if rec["pages"] == rec["ref_pages"] else f" ⚠ 쪽 수 {rec['pages']}/{rec['ref_pages']}"
            print(f"  {np.mean(sc):5.1f}%  최저 {min(sc):5.1f}  {GOOD:.0f}%↑ {sum(s >= GOOD for s in sc):3d}/{len(sc):<3d}"
                  f"  {rec['name']}{pc}")
    recs.sort(key=lambda r: r["name"])
    allp = [s for r in recs for s in r.get("scores", [])]
    if allp:
        same = sum(1 for r in recs if r.get("pages") == r.get("ref_pages"))
        print(f"\n쪽 {len(allp)}개: 평균 {np.mean(allp):.2f}% · 중앙 {np.median(allp):.2f}% · "
              f"{GOOD:.0f}% 이상 {100 * np.mean([s >= GOOD for s in allp]):.1f}% · 쪽 수 일치 {same}/{len(recs)}")
        if diff_dir:
            print(f"겹친 그림: {diff_dir}")
    return recs


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_rhwp_pairs() -> list[tuple[Path, Path]]:
    lst = json.loads(LIST.read_text(encoding="utf-8"))
    dest = WORK / "rhwp"
    dest.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/{lst['repo']}/{lst['commit']}/"
    pairs = []
    need = [(p, k) for p in lst["pairs"] for k in ("doc", "pdf")]
    got = 0
    for p, k in need:
        name = Path(p[k]).name
        # 쌍마다 이름을 맞춘다 — 한 원본에 한컴 판별 PDF 가 여럿인 경우가 있다
        stem = Path(p["pdf"]).stem
        local = dest / (stem + Path(name).suffix.lower())
        if local.is_file() and git_blob(local.read_bytes()) == p[k + "_blob"]:
            continue
        url = base + urllib.parse.quote(p[k])
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        if git_blob(data) != p[k + "_blob"]:
            raise SystemExit(f"지문이 다릅니다: {p[k]}")
        local.write_bytes(data)
        got += 1
    if got:
        print(f"rhwp 시험 문서 {got}개를 받았습니다 → {dest}")
    for p in lst["pairs"]:
        stem = Path(p["pdf"]).stem
        pairs.append((dest / (stem + Path(p["doc"]).suffix.lower()), dest / (stem + ".pdf")))
    return pairs


# ── 기준선 (PDF OCR 의 정답지 채점과 같은 역할) ─────────────────────

BASELINE = WORK / "기준.json"
# 이만큼 떨어지면 '나빠짐' — 측정 잡음(같은 엔진 재실행은 0)보다 크고, 고침 한 건의 효과보다 작다
DROP_SIL = 0.3       # 실루엣 일치율 %p
DROP_MATCH = 0.01    # 칸 정렬 점수
DROP_RECALL = 0.005  # 한글 글자·낱말 회수율


def measure_doc(path: Path) -> dict:
    """문서 하나를 실제 도구 경로로 변환해 기준선 항목을 잰다(쪽 수·글자층·1쪽 한컴 미리보기)."""
    import fidelity
    from rhwp_cli import Rhwp
    rec = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_base_", dir=hwp2pdf.TMP_ROOT, ignore_cleanup_errors=True) as td:
        td = Path(td)
        out = td / "out.pdf"
        cfg = dict(hwp2pdf.DEFAULTS, 미리보기_대조=True)
        res = hwp2pdf.convert_one(path, out, cfg, Rhwp().version(), td / "job", path.name)
        rec.update(pages=res["pages"], hangul=res.get("hangul"), hangul_words=res.get("hangul_words"),
                   match=res.get("preview"))
        thumb = td / "job" / "preview.png"  # convert_one 이 미리보기 대조에 쓴 그림
        if thumb.is_file():
            from PIL import Image
            with Image.open(thumb) as im:
                small = min(im.size) < SIL_MIN_PX
            rec["silhouette"] = None if small else fidelity.preview_silhouette(out, thumb)
    return rec


def compare_baseline(old: dict, new: dict) -> list[str]:
    """old → new 에서 나빠진 항목의 설명. 원본 파일이 바뀌었으면 비교하지 않고 그 사실만 알린다."""
    if old.get("sha256") != new.get("sha256"):
        return ["원본 파일이 바뀜 — 비교하지 않음(--기준 갱신 으로 새로 잡으세요)"]
    bad = []
    if old.get("pages") != new.get("pages"):
        bad.append(f"쪽 수 {old.get('pages')} → {new.get('pages')}")
    for key, drop, label in (("silhouette", DROP_SIL, "실루엣"), ("match", DROP_MATCH, "칸 정렬"),
                             ("hangul", DROP_RECALL, "한글 글자"), ("hangul_words", DROP_RECALL, "한글 낱말")):
        a, b = old.get(key), new.get(key)
        if a is not None and (b is None or a - b > drop):
            bad.append(f"{label} {a:.3f} → {'없음' if b is None else f'{b:.3f}'}")
    return bad


def run_baseline(action: str | None, files: list[Path]) -> int:
    from rhwp_cli import Rhwp
    data = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {"문서": {}}
    docs = data["문서"]
    if action == "추가":
        if not files:
            print("추가할 한글 문서를 주세요: 원본_대조.py --기준 추가 <파일...>")
            return 1
        for f in files:
            docs.setdefault(str(f.resolve()), {})
        targets = [f.resolve() for f in files]
    else:
        targets = [Path(p) for p in docs]
    if not targets:
        print("기준선이 비어 있습니다. 먼저: 원본_대조.py --기준 추가 <파일...>")
        return 1
    engine = Rhwp().version()
    worse = 0
    print(f"기준선 {len(targets)}개 · 엔진 {engine}\n")
    for p in targets:
        key = str(p)
        if not p.is_file():
            print(f"  -  찾을 수 없음: {p}")
            continue
        try:
            new = measure_doc(p)
        except Exception as e:  # 암호·손상 등 — 기준선을 지우지 않고 알린다
            print(f"  -  {p.name}: {str(e)[:120]}")
            continue
        old = docs.get(key, {}).get("점수")
        sil = "-" if new.get("silhouette") is None else f"{new['silhouette']:.2f}%"
        line = (f"{p.name}: {new['pages']}쪽 · 실루엣 {sil} · 칸 정렬 {new.get('match') or 0:.3f} · "
                f"글자층 {100 * (new.get('hangul') or 0):.0f}%/{100 * (new.get('hangul_words') or 0):.0f}%")
        if old and action != "갱신":
            bad = compare_baseline(old, new)
            if bad:
                worse += 1
                print(f"  ⚠ {line}\n      나빠짐: " + " · ".join(bad))
            else:
                prev = old.get("silhouette")
                delta = "" if prev is None or new.get("silhouette") is None else f" ({new['silhouette'] - prev:+.2f})"
                print(f"  ✔ {line}{delta}")
        else:
            print(f"  ● {line}  (기준으로 저장)")
        if action in ("추가", "갱신") or not old:
            docs[key] = {"점수": new, "엔진": engine, "날짜": time.strftime("%Y-%m-%d %H:%M")}
    WORK.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if worse:
        print(f"\n나빠진 문서 {worse}개 — 의도한 변화면: 원본_대조.py --기준 갱신")
    return 1 if worse else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="변환 결과를 한컴 원본과 맞대 일치율을 잰다")
    ap.add_argument("대상", nargs="*", type=Path, help="한글 문서 또는 폴더 (미리보기 대조)")
    ap.add_argument("--쌍", type=Path, metavar="폴더", help="한컴 PDF 쌍 폴더")
    ap.add_argument("--rhwp-시험", dest="rhwp", action="store_true", help="rhwp 저장소의 한컴 PDF 34쌍")
    ap.add_argument("--차이", action="store_true", help="90%% 미만 쪽의 겹친 그림을 남긴다")
    ap.add_argument("--json", type=Path, metavar="파일", help="결과 JSON")
    ap.add_argument("--기준", dest="baseline", nargs="?", const="비교", choices=["비교", "추가", "갱신"],
                    help="기준선: 비교(기본) · 추가 <파일...> · 갱신")
    a = ap.parse_args(argv)
    _no_prompt()
    hwp2pdf.TMP_ROOT.mkdir(exist_ok=True)
    hwp2pdf.cleanup_stale_temp()  # 중간에 끊긴 대조가 남긴 작업 폴더(원본 사본 포함)를 도구와 같은 규칙으로 치운다

    if a.baseline:
        return run_baseline(None if a.baseline == "비교" else a.baseline, a.대상)

    from rhwp_cli import Rhwp
    print(f"rhwp {Rhwp().version()}")
    if a.rhwp:
        recs = run_pairs(fetch_rhwp_pairs(), a.차이)
    elif a.쌍:
        recs = run_pairs(find_pairs(a.쌍), a.차이)
    else:
        recs = run_preview(a.대상 or [hwp2pdf.IN_DIR])
    if a.json:
        a.json.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if recs and not all("err" in r for r in recs) else 1


if __name__ == "__main__":
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
