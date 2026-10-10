# -*- coding: utf-8 -*-
"""
HWP to PDF 엔진
"HWP 입력" 폴더(또는 바로가기에 끌어다 놓은 파일·폴더)의 한글 문서(.hwp/.hwpx)를
"PDF 출력" 폴더에 PDF 로 저장한다. 한컴오피스 없이, 인터넷 없이, 이 PC 안에서만 돈다.

그리기는 rhwp(오픈소스 HWP 렌더러)가 하고, 이 엔진은
  - 큰 문서를 쪽마다 나눠 동시에 그리고 (rhwp_cli.export_pdf_pages)
  - rhwp PDF 의 깨진 글자층을 바로잡고 (pdf_text — 글자 탈락·띄어쓰기 소실)
  - 결과를 스스로 잰다 (fidelity — 한컴 미리보기 대조, 글자층 회수율)
"""
from __future__ import annotations

import configparser
import contextlib
import datetime
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ENGINE_DIR = Path(__file__).resolve().parent
BASE = ENGINE_DIR.parent
IN_DIR = BASE / "HWP 입력"
OUT_DIR = BASE / "PDF 출력"
CONFIG_FILE = BASE / "설정.ini"
LOG_FILE = BASE / "실행기록.txt"
# 작업용 임시 폴더도 도구 폴더 안에 둔다 — 강제 종료되어도 흔적이 폴더 밖에 남지 않는다
TMP_ROOT = BASE / ".tmp"

HWP_EXTS = {".hwp", ".hwpx"}

# 엔진 판. 변환 결과를 바꾸는 고침을 하면 올린다 — 이미 만든 PDF 가 다음 실행에서 새로 만들어진다.
ENGINE_REV = 1

# 산출물 맨 끝(%%EOF 뒤)에 붙이는 한 줄. 우리가 만든 PDF 인지, 그 뒤로 누가 고쳤는지를
# 이것으로 가린다 — 뷰어가 주석을 저장하면 이 줄 뒤에 덧붙이거나 파일을 새로 써서 사라진다.
MARKER = b"%HWP2PDF "

# 한컴 미리보기 대조 점수가 이보다 낮으면 '모양이 많이 다르다' 고 알린다.
# 근거: README 「실측」 — rhwp 공개 시험 문서와 사용자 문서에서 정상 변환의 분포 아래쪽.
PREVIEW_WARN = 0.75
# 글자층에서 한글 글자가 이보다 적게 남으면 알린다 (보정 후 실측 0.96~1.00)
TEXT_WARN = 0.95


# ── 기록 ────────────────────────────────────────────────────────────

def log(msg: str, echo: bool = True):
    if echo:
        print(msg, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg.strip()}\n")
    except OSError:
        pass


def acquire_lock():
    """중복 실행 방지 잠금. 이미 실행 중이면 None, 잠글 수 없는 환경이면 False."""
    import msvcrt
    try:
        f = open(ENGINE_DIR / ".lock", "w")
    except OSError:
        return False
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        return f
    except OSError:
        f.close()
        return None


# ── 설정 ────────────────────────────────────────────────────────────

DEFAULTS = {"저장_위치": "출력폴더", "미리보기_대조": True}


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    cfg = dict(DEFAULTS)
    if not path.exists():
        return cfg
    parser = configparser.ConfigParser(interpolation=None)
    try:
        try:
            parser.read(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            parser.read(path, encoding="cp949")
    except (configparser.Error, UnicodeDecodeError, OSError) as e:
        log(f"⚠ 설정.ini를 읽을 수 없어 기본값으로 진행합니다. ({e})")
        return cfg
    section = next((n for n in parser.sections() if n.strip().lower() == "settings"), None)
    if section is None:
        log("⚠ 설정.ini에 [settings] 섹션이 없어 기본값으로 진행합니다.")
        return cfg
    s = parser[section]

    raw = s.get("저장_위치", "출력폴더").strip().replace(" ", "")
    if raw in ("출력폴더", "원본옆"):
        cfg["저장_위치"] = raw
    else:
        log(f"⚠ 설정.ini의 저장_위치 '{raw}'를 알 수 없어 출력폴더로 진행합니다. "
            f"쓸 수 있는 값: 출력폴더 / 원본옆")

    raw = s.get("미리보기_대조", "켬").strip().lower()
    if raw in ("켬", "on", "yes", "끔", "off", "no"):
        cfg["미리보기_대조"] = raw in ("켬", "on", "yes")
    else:
        log(f"⚠ 설정.ini의 미리보기_대조 '{raw}'를 알 수 없어 켬으로 진행합니다. 쓸 수 있는 값: 켬 / 끔")
    return cfg


def with_font_substitution(rhwp_version: str) -> str:
    """대체 글꼴이 없어 다른 글꼴로 바꿔 그리면 알리고, 엔진 표기에 그 사실을 붙인다.

    표기는 산출물 지문에 들어가므로, 나중에 그 글꼴을 설치하면 이미 만든 PDF 도 다시 만든다.
    """
    from rhwp_cli import pdf_font_args
    args, notes = pdf_font_args()
    for note in notes:
        log(f"⚠ {note}")
    if args:
        pairs = [f"{args[i].removeprefix('--fallback-')}={args[i + 1]}" for i in range(0, len(args), 2)]
        rhwp_version += f" ({', '.join(pairs)})"
    return rhwp_version


def fingerprint(rhwp_version: str) -> str:
    # PDF 내용을 바꾸는 것만 넣는다 — 미리보기 대조·저장 위치는 결과물이 같다
    return f"rev{ENGINE_REV}|rhwp{rhwp_version}"


# ── 산출물 표식 ──────────────────────────────────────────────────────

def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def file_sha16(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def append_marker(pdf_bytes: bytes, meta: dict) -> bytes:
    if not pdf_bytes.endswith(b"\n"):
        pdf_bytes += b"\n"
    meta = dict(meta, body=sha16(pdf_bytes))
    return pdf_bytes + MARKER + json.dumps(meta, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


def read_marker(path: Path):
    """('ours', 메타) — 우리가 만든 그대로 / ('edited', 메타) — 만든 뒤 바뀜 /
    ('foreign', None) — 우리가 만든 것이 아님 / ('missing', None)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "foreign", None
    k = data.rfind(MARKER, max(0, len(data) - 65536))
    if k < 0:
        return "foreign", None
    line = data[k:]
    end = line.find(b"\n")
    try:
        meta = json.loads(line[len(MARKER):end if end >= 0 else None].decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return "foreign", None
    rest = line[end + 1:] if end >= 0 else b""
    if rest.strip() or meta.get("body") != sha16(data[:k]):
        return "edited", meta
    return "ours", meta


# ── 대상 정하기 ──────────────────────────────────────────────────────

def is_hwp(p: Path) -> bool:
    return (p.suffix.lower() in HWP_EXTS and not p.name.startswith("~$")
            and not p.name.startswith("."))


def scan(folder: Path) -> list[Path]:
    out = []
    for f in sorted(folder.rglob("*")):
        try:
            if f.is_file() and is_hwp(f):
                out.append(f)
        except OSError:
            continue
    return out


def assign_outputs(items: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """(원본, 원하는 PDF 경로) 목록에서 겹치는 PDF 이름을 풀어 준다.

    같은 폴더에 '기말.hwp' 와 '기말.hwpx' 가 함께 있으면 둘 다 '기말.pdf' 가 된다 —
    이때는 둘 모두에 원래 형식을 붙인다(먼저 온 쪽만 특별대우하지 않는다).
    """
    key = lambda p: str(p).lower()  # noqa: E731
    dup = {k for k, c in Counter(key(o) for _, o in items).items() if c > 1}
    used, out = set(), []
    for src, o in items:
        if key(o) in dup:
            o = o.with_name(f"{o.stem} ({src.suffix.lstrip('.').lower()}).pdf")
        n, cand = 2, o
        while key(cand) in used:
            cand = o.with_name(f"{o.stem} ({n}).pdf")
            n += 1
        used.add(key(cand))
        out.append((src, cand))
    return out


def plan_targets(cfg: dict, dropped: list[Path] | None = None):
    """무엇을 어디로 변환할지 정한다. 파일시스템을 바꾸지 않는다.

    dropped 가 있으면(끌어다 놓기·보내기) 그것만 다룬다. 폴더를 놓으면 그 안의 한글 문서를
    폴더 구조 그대로 옮긴다.
    """
    beside = cfg["저장_위치"] == "원본옆"
    items, ignored = [], []
    if dropped is None:
        for f in scan(IN_DIR):
            rel = f.relative_to(IN_DIR)
            items.append((f, (f.parent if beside else OUT_DIR / rel.parent) / (f.stem + ".pdf")))
        try:
            ignored = [f for f in IN_DIR.rglob("*") if f.is_file() and not is_hwp(f)
                       and not f.name.startswith(("~$", "."))]
        except OSError:
            ignored = []
    else:
        for p in dropped:
            if p.is_dir():
                for f in scan(p):
                    rel = f.relative_to(p)
                    dest = f.parent if beside else OUT_DIR / p.name / rel.parent
                    items.append((f, dest / (f.stem + ".pdf")))
            elif p.is_file() and is_hwp(p):
                items.append((p, (p.parent if beside else OUT_DIR) / (p.stem + ".pdf")))
            else:
                ignored.append(p)
    return assign_outputs(items), ignored


def triage(pairs, fp: str):
    """이미 만든 것은 건너뛰고, 우리 것이 아닌 PDF 는 덮어쓰지 않는다."""
    todo, skipped, blocked, rebuilt = [], [], [], []
    for src, out in pairs:
        state, meta = read_marker(out)
        if state == "foreign":
            blocked.append((src, out))
            continue
        if state == "edited":
            skipped.append((src, "PDF 가 변환 뒤에 수정되어 보존합니다 — 새로 만들려면 그 PDF 를 지우세요"))
            continue
        if state == "ours":
            try:
                same = (meta.get("bytes") == src.stat().st_size and meta.get("src_sha") == file_sha16(src))
            except OSError:
                same = False
            if same and meta.get("cfg") == fp:
                skipped.append((src, "이미 변환됨"))
                continue
            rebuilt.append(src)
        todo.append((src, out))
    return todo, skipped, blocked, rebuilt


# ── 변환 ────────────────────────────────────────────────────────────

def workers_for(pages: int) -> int:
    n = (os.cpu_count() or 4) - 2
    return max(1, min(n, 8, pages))


def compat_for(info: dict) -> str | None:
    """마지막으로 저장한 한컴 세대에 맞춰 조판한다 — 문서에 저장된 줄 배치가 그 세대의 것이다."""
    prod = str((info.get("lastSavedWith") or {}).get("product", ""))
    return "2024" if "2024" in prod else None


def ask_password(name: str, tries: int) -> str | None:
    """암호를 묻는다. Enter 만 누르면 None(건너뜀). 창이 없는 실행이면 묻지 않는다."""
    if not sys.stdin or not sys.stdin.isatty():
        return None
    import getpass
    prompt = (f"  🔒 '{name}' 은 암호가 걸린 문서입니다. 암호 (건너뛰려면 Enter): " if tries == 0
              else "  암호가 맞지 않습니다. 다시 입력 (건너뛰려면 Enter): ")
    try:
        pw = getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    return pw or None


def open_with_password(rh_cls, src: Path, display: str):
    """문서 정보를 읽는다. 암호 문서면 물어서 연다. (Rhwp, info) 또는 예외."""
    from rhwp_cli import PasswordNeeded, WrongPassword
    rh = rh_cls()
    try:
        return rh, rh.info(src)
    except PasswordNeeded:
        pass
    for tries in range(3):
        pw = ask_password(display, tries)
        if pw is None:
            raise PasswordNeeded("암호가 걸린 문서입니다 — 암호를 입력하지 않아 건너뛰었습니다")
        rh = rh_cls(password=pw)
        try:
            return rh, rh.info(src)
        except (WrongPassword, PasswordNeeded):
            continue
    raise WrongPassword("암호가 세 번 맞지 않아 건너뛰었습니다")


def convert_one(src: Path, out: Path, cfg: dict, rhwp_version: str, job: Path, display: str) -> dict:
    """문서 하나를 변환해 out 에 쓴다. 결과 요약을 돌려준다. 실패하면 예외."""
    import pikepdf
    import pypdfium2 as pdfium
    import fidelity
    import pdf_text
    from rhwp_cli import Rhwp, export_pdf_pages

    t0 = time.monotonic()
    job.mkdir(parents=True, exist_ok=True)
    # 원본을 작업 폴더로 복사해 쓴다 — 변환 도중 원본이 바뀌어도 한 시점의 내용으로 끝까지 가고,
    # 표식에 남기는 지문도 실제로 변환한 바로 그 내용의 것이 된다.
    work = job / ("in" + src.suffix.lower())
    shutil.copyfile(src, work)
    src_sha, src_bytes = file_sha16(work), work.stat().st_size

    rh, info = open_with_password(Rhwp, work, display)
    pages = int(info.get("pageCount") or 0)
    compat = compat_for(info)
    if pages <= 0:
        raise RuntimeError("문서에 쪽이 없습니다")

    if pages >= 20:
        print(f"  {pages}쪽 — 쪽마다 나눠 그리는 중…", flush=True)

    # 1. 그리기 — 두 쪽 이상이면 쪽마다 동시에
    nw = workers_for(pages)
    built = job / "built.pdf"
    tstat = Counter()
    # 실패해도 연 PDF 를 닫는다 — 열린 채로 두면 Windows 가 작업 폴더를 지우지 못한다
    with contextlib.ExitStack() as stack:
        if pages >= 2 and nw >= 2:
            parts, warns = export_pdf_pages(rh, work, job / "pages", pages, nw, compat)
            pdf = stack.enter_context(pikepdf.new())
            for p in parts:
                part = stack.enter_context(pikepdf.open(p))
                if len(part.pages) != 1:
                    raise RuntimeError(f"rhwp 가 한 쪽 대신 {len(part.pages)}쪽을 냈습니다")
                pdf.pages.extend(part.pages)
        else:
            _, warns = rh.export_pdf(work, job / "raw.pdf", compat=compat)
            pdf = stack.enter_context(pikepdf.open(job / "raw.pdf"))
        if len(pdf.pages) != pages:
            raise RuntimeError(f"쪽 수가 맞지 않습니다 (문서 {pages}쪽, PDF {len(pdf.pages)}쪽)")

        # 2. 글자층 바로잡기
        try:
            trees = rh.render_trees(work, job / "rt", compat)
        except Exception:
            trees = []
        if len(trees) != pages:
            trees = [None] * pages
        for page, tree in zip(pdf.pages, trees):
            st = pdf_text.fix_page(pdf, page, tree)
            tstat.update({k: v for k, v in st.items() if isinstance(v, (int, bool))})

        pdf.docinfo["/Title"] = pikepdf.String(src.stem)
        pdf.docinfo["/Subject"] = pikepdf.String(f"원본: {src.name}")
        pdf.docinfo["/Creator"] = pikepdf.String(f"HWP to PDF rev{ENGINE_REV}")
        pdf.docinfo["/Producer"] = pikepdf.String(f"rhwp {rhwp_version} + pikepdf")
        pdf.Root.Lang = pikepdf.String("ko-KR")
        pdf.Root.ViewerPreferences = pikepdf.Dictionary(DisplayDocTitle=True)
        # deterministic_id: /ID 를 내용에서 만든다 — 기본값은 저장마다 무작위라, 같은 원본·같은 엔진이
        # 같은 바이트를 내지 않았다(새 PC 설치 검증에서 산출물을 바이트로 대조하려면 필요하다).
        pdf.save(built, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 deterministic_id=True)

    # 3. 스스로 재기
    res = dict(pages=pages, warns=warns, flattened=int(tstat["flattened"]), spaces=int(tstat["spaces"]))
    try:
        reference = "\n".join(rh.export_text(work, job / "txt"))
        doc = pdfium.PdfDocument(str(built))
        try:
            extracted = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
        finally:
            doc.close()
        res["hangul"], res["hangul_words"] = fidelity.text_recall(reference, extracted)
    except Exception as e:
        res["text_err"] = str(e)[:120]
    if cfg["미리보기_대조"]:
        thumb = job / "preview.png"
        if rh.thumbnail(work, thumb):
            try:
                res["preview"] = fidelity.preview_match(built, thumb)
            except Exception as e:
                res["preview_err"] = str(e)[:120]

    # 4. 표식을 붙여 제자리에 (임시 이름으로 쓴 뒤 바꿔치기 — 도중에 끊겨도 반쪽 PDF 가 남지 않는다)
    meta = dict(v=1, src=src.name, bytes=src_bytes, src_sha=src_sha, cfg=fingerprint(rhwp_version),
                pages=pages, made=datetime.datetime.now().isoformat(timespec="seconds"))
    data = append_marker(built.read_bytes(), meta)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    res["seconds"] = time.monotonic() - t0
    res["bytes"] = len(data)
    return res


def summarize(res: dict) -> tuple[str, list[str]]:
    """한 줄 요약과 '확인 권장' 목록.

    확인 권장은 잰 값이 문턱을 넘을 때만 낸다. rhwp 의 조판 경고(표 겹침 등)는 요약 줄에만
    적는다 — 실측에서 저자가 일부러 겹쳐 둔 답안 표에도 나왔고, 그 문서의 미리보기 대조는
    정상이었다(0.81).
    """
    bits = [f"{res['pages']}쪽", f"{res['seconds']:.1f}초"]
    notes = []
    pv = res.get("preview")
    if pv is not None:
        bits.append(f"한컴 미리보기 일치 {pv:.2f}")
        if pv < PREVIEW_WARN:
            notes.append(f"1쪽 모양이 한컴 미리보기와 많이 다릅니다({pv:.2f}) — 원본과 비교해 보세요")
    hc = res.get("hangul")
    if hc is not None:
        bits.append(f"글자층 {hc * 100:.0f}%")
        if hc < TEXT_WARN:
            notes.append(f"PDF 에서 뽑히는 한글이 본문의 {hc * 100:.0f}% 뿐입니다 — 복사·검색이 불완전할 수 있습니다")
    if res.get("warns"):
        bits.append("rhwp 경고 " + ", ".join(sorted({w.split(":")[0] for w in res["warns"]})))
    return " · ".join(bits), notes


def cleanup_stale_temp():
    """지난 실행이 강제 종료되며 남긴 작업 폴더·반쪽 파일을 치운다 (6시간 지난 것만)."""
    cutoff = time.time() - 6 * 3600
    try:
        for d in TMP_ROOT.glob("hwp2pdf_*"):
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass
    for root in (OUT_DIR,):
        try:
            for t in root.rglob("*.pdf.tmp"):
                if t.stat().st_mtime < cutoff:
                    t.unlink(missing_ok=True)
        except OSError:
            pass


_awake = False


def keep_awake(on: bool):
    """변환하는 동안 PC 가 절전으로 들어가지 않게 한다."""
    global _awake
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetThreadExecutionState.restype = ctypes.c_uint
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        k32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        _awake = on
    except Exception:
        pass


def reveal(outputs: list[Path]):
    """결과를 찾아 들어가지 않아도 되게 탐색기를 연다. 하나면 그 파일을 골라 둔 채로."""
    try:
        if len(outputs) == 1:
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(outputs[0])])
            return
        parents = {p.parent for p in outputs}
        if len(parents) == 1:
            os.startfile(parents.pop())
        elif all(OUT_DIR in p.parents for p in outputs):
            os.startfile(OUT_DIR)
        else:                       # 원본 옆에 저장해 여러 폴더에 흩어졌다 — 첫 결과가 있는 곳
            os.startfile(outputs[0].parent)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    started = time.monotonic()
    print("=" * 58)
    print(" HWP to PDF — 한글 문서를 PDF 로")
    print("=" * 58)

    lock = acquire_lock()
    if lock is None:
        print("\nHWP to PDF 가 이미 실행 중입니다. 기존 창의 작업이 끝난 뒤 다시 실행해주세요.")
        return 0
    if lock is False:
        print("⚠ 중복 실행 방지 잠금을 걸 수 없습니다. 창을 두 개 열지 않도록 주의하세요.")

    from rhwp_cli import RHWP_EXE, Rhwp, RhwpError, PasswordNeeded, WrongPassword
    if not RHWP_EXE.is_file():
        print(f"\n✘ 변환 엔진(rhwp)이 없습니다: {RHWP_EXE}")
        print("  이 폴더의 \"환경 설치.bat\" 을 실행하면 받아 옵니다.")
        return 1
    try:
        rhwp_version = Rhwp().version()
    except RhwpError as e:
        print(f"\n✘ 변환 엔진(rhwp)을 실행할 수 없습니다: {e}")
        return 1
    rhwp_version = with_font_substitution(rhwp_version)

    for d in (IN_DIR, OUT_DIR):
        d.mkdir(exist_ok=True)
    cleanup_stale_temp()
    cfg = load_config()

    dropped = [Path(a) for a in argv] or None
    pairs, ignored = plan_targets(cfg, dropped)
    if dropped:
        print(f"\n끌어다 놓은 {len(dropped)}개에서 한글 문서 {len(pairs)}개를 찾았습니다.")
    fp = fingerprint(rhwp_version)
    todo, skipped, blocked, rebuilt = triage(pairs, fp)

    if skipped:
        print(f"건너뜀 {len(skipped)}개:")
        for f, why in skipped[:10]:
            print(f"  - {f.name} ({why})")
        if len(skipped) > 10:
            print(f"  ... 외 {len(skipped) - 10}개")
    if blocked:
        print("⚠ 아래는 같은 이름의 PDF 가 이미 있고 이 도구가 만든 것이 아니라 건너뜁니다 "
              "(덮어쓰지 않았습니다). 그 PDF 를 옮기거나 이름을 바꾼 뒤 다시 실행하세요:")
        for f, out in blocked:
            print(f"  - {f.name} → {out}")
    if ignored:
        names = ", ".join(p.name for p in ignored[:5])
        print(f"무시한 파일 {len(ignored)}개 (한글 문서가 아님): {names}{' ...' if len(ignored) > 5 else ''}")
    if not todo:
        print("\n변환할 새 문서가 없습니다." if (pairs or ignored or dropped)
              else f"\n\"{IN_DIR.name}\" 폴더에 .hwp / .hwpx 파일을 넣고 다시 실행해주세요.")
        return 0

    print(f"\n변환 대상 {len(todo)}개 · rhwp {rhwp_version}"
          + (f" · 그중 {len(rebuilt)}개는 원본·엔진이 바뀌어 다시 만듭니다" if rebuilt else ""))
    log(f"── 실행 시작 · 대상 {len(todo)}개 · 건너뜀 {len(skipped)}개 · {fp}", echo=False)
    keep_awake(True)

    ok, failures, outputs, flagged = 0, [], [], []
    TMP_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hwp2pdf_", dir=TMP_ROOT, ignore_cleanup_errors=True) as td:
        for i, (src, out) in enumerate(todo, 1):
            display = src.name
            print(f"\n[{i}/{len(todo)}] {display}", flush=True)
            try:
                res = convert_one(src, out, cfg, rhwp_version, Path(td) / f"job{i}", display)
            except KeyboardInterrupt:
                log(f"  ⚠ 사용자가 중단했습니다 (진행 중이던 파일: {display})", echo=False)
                raise
            except (PasswordNeeded, WrongPassword) as e:
                failures.append((display, str(e)))
                log(f"  ✘ {display} — {e}")
                continue
            except RhwpError as e:
                failures.append((display, str(e)))
                log(f"  ✘ {display} — {e}")
                continue
            except Exception as e:
                msg = str(e) or type(e).__name__
                failures.append((display, msg))
                log(f"  ✘ {display} — {type(e).__name__}: {msg}")
                continue
            line, notes = summarize(res)
            ok += 1
            outputs.append(out)
            log(f"  ✔ {display} → {out.name} · {line}")
            for n in notes:
                log(f"    ⚠ {n}")
            if notes:
                flagged.append((display, notes))
            shutil.rmtree(Path(td) / f"job{i}", ignore_errors=True)

    keep_awake(False)
    log(f"\n전체 완료: 성공 {ok}개 / 실패 {len(failures)}개"
        + (f" / 확인 권장 {len(flagged)}개" if flagged else ""))
    if failures:
        print("실패 목록:")
        for name, err in failures:
            print(f"  ✘ {name}\n      {err}")
    if flagged:
        print("확인 권장 (변환은 됐지만 원본과 달라 보이는 곳이 있음):")
        for name, notes in flagged:
            print(f"  ⚠ {name}")
            for n in notes:
                print(f"      {n}")
    if failures or flagged:
        print(f"\n기록은 {LOG_FILE.name}에 남아 있습니다.")
    print(f"결과 위치: {OUT_DIR if cfg['저장_위치'] == '출력폴더' else '각 원본 옆'}")

    if outputs:
        reveal(outputs)
    # 큰 묶음은 오래 걸린다 — 자리를 비웠어도 끝났음을 알 수 있게 1분을 넘긴 경우만 소리로 알린다
    if time.monotonic() - started > 60:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ENGINE_DIR))
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n\n중단했습니다. 끝난 문서는 저장되어 있고, 진행 중이던 문서는 다음 실행에서 다시 합니다.")
        sys.exit(1)
    finally:
        if _awake:
            keep_awake(False)
