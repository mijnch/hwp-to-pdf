# -*- coding: utf-8 -*-
"""rhwp 명령줄 도구 호출.

rhwp(https://github.com/edwardkim/rhwp, MIT)는 한컴 없이 HWP/HWPX 를 읽고 그리는
Rust 프로그램이다. 이 도구는 그 실행 파일을 engine\\rhwp 에 두고 부른다 — 릴리스(setup_env.py)
또는 이 도구의 고침을 넣어 만든 판(build_rhwp.py, 출처는 engine\\rhwp\\BUILD.json).
명령은 전부 '파일 하나 → 결과 파일' 이라 하위 프로세스로 충분하다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent
RHWP_EXE = ENGINE_DIR / "rhwp" / "rhwp.exe"

# 콘솔 창을 띄우지 않는다 (bat 창 안에서 돌 때도 하위 창이 깜빡이지 않게)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def build_tag(exe: Path = RHWP_EXE) -> str | None:
    """실행 파일이 BUILD.json 의 고친 판이면 '바탕커밋.p고침지문' (예: 21ed640.p3f9a1c), 아니면 None.

    판 표기(0.8.7)는 릴리스와 같으므로 이것을 덧붙여야 엔진이 바뀐 것을 안다 — 산출물 표식의
    지문에 들어가 고친 판으로 바꾸면 이미 만든 PDF 도 새로 만든다.
    """
    exe = Path(exe)
    try:
        info = json.loads(exe.with_name("BUILD.json").read_text(encoding="utf-8"))
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != info["exe_sha256"]:
            return None          # 표지가 다른 실행 파일을 가리킨다 (나중에 릴리스로 바꿈 등)
        return f"{info['base'].rsplit('@', 1)[-1][:7]}.p{info['patch_sha256'][:6]}"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


class RhwpError(Exception):
    """rhwp 가 문서를 처리하지 못했다. 메시지는 사용자에게 그대로 보여 줄 한국어."""


class PasswordNeeded(RhwpError):
    pass


class WrongPassword(RhwpError):
    pass


def _clean(text: str) -> str:
    """rhwp 출력에서 사용자에게 보일 마지막 오류 한 줄을 고른다."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errs = [ln for ln in lines if ln.startswith("오류")]
    msg = (errs or lines or ["알 수 없는 오류"])[-1]
    msg = re.sub(r"^오류:\s*", "", msg)
    msg = re.sub(r"\s*\(--password <pw> 로 전달\)", "", msg)
    msg = re.sub(r"\s*오류코드: \w+\.?", "", msg)
    return msg


def _raise_for(text: str):
    msg = _clean(text)
    if "비밀번호가 필요" in msg:
        raise PasswordNeeded("암호가 걸린 문서입니다")
    if "비밀번호가 일치하지" in msg:
        raise WrongPassword("암호가 맞지 않습니다")
    if "지원하지 않는 포맷" in msg or "알 수 없는 파일 형식" in msg:
        raise RhwpError("HWP 문서로 읽을 수 없습니다 (형식이 다르거나 파일이 손상됨)")
    raise RhwpError(msg)


class Rhwp:
    def __init__(self, exe: Path = RHWP_EXE, password: str | None = None, timeout: int = 1800):
        self.exe = Path(exe)
        self.password = password
        self.timeout = timeout

    def run(self, args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        cmd = [str(self.exe)] + args
        feed = {"stdin": subprocess.DEVNULL}
        if self.password is not None:
            # 명령줄 인자로 넘기면 프로세스 목록에 암호가 보인다 — 표준 입력으로 준다
            cmd.append("--password-stdin")
            feed = {"input": (self.password + "\n").encode("utf-8")}
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout,
                               creationflags=_NO_WINDOW, **feed)
        except subprocess.TimeoutExpired:
            raise RhwpError(f"rhwp 가 {timeout or self.timeout}초 안에 끝나지 않았습니다")
        except OSError as e:
            raise RhwpError(f"rhwp 를 실행할 수 없습니다: {e}")
        p.stdout = p.stdout.decode("utf-8", "replace")
        p.stderr = p.stderr.decode("utf-8", "replace")
        if p.returncode != 0:
            _raise_for(p.stdout + "\n" + p.stderr)
        return p

    def version(self) -> str:
        """판 표기. 고친 판이면 '0.8.7+21ed640.p3f9a1c' 처럼 출처를 덧붙인다."""
        p = self.run(["--version"], timeout=60)
        m = re.search(r"v?(\d+\.\d+\.\d+)", p.stdout + p.stderr)
        ver = m.group(1) if m else (p.stdout.strip() or "?")
        tag = build_tag(self.exe)
        return f"{ver}+{tag}" if tag else ver

    def info(self, src: Path) -> dict:
        p = self.run(["info", str(src), "--json"], timeout=300)
        try:
            return json.loads(p.stdout)
        except ValueError:
            _raise_for(p.stdout + "\n" + p.stderr)

    def export_pdf(self, src: Path, dst: Path, page: int | None = None,
                   compat: str | None = None) -> tuple[dict, list[str]]:
        """PDF 로 내보낸다. (매니페스트, 경고 줄들) 을 돌려준다."""
        args = ["export-pdf", str(src), "-o", str(dst), "--json"]
        if page is not None:
            args += ["-p", str(page)]
        if compat:
            args += ["--compat", compat]
        p = self.run(args)
        man = {}
        for ln in reversed(p.stdout.splitlines()):
            if ln.startswith("{"):
                try:
                    man = json.loads(ln)
                    break
                except ValueError:
                    pass
        if not dst.is_file() or dst.stat().st_size == 0:
            _raise_for(p.stdout + "\n" + p.stderr)
        return man, warnings_of(p.stderr + "\n" + p.stdout)

    def render_trees(self, src: Path, outdir: Path, compat: str | None = None) -> list[dict]:
        """쪽마다 렌더 트리(줄·낱말 묶음과 위치)를 돌려준다."""
        outdir.mkdir(parents=True, exist_ok=True)
        args = ["export-render-tree", str(src), "-o", str(outdir) + os.sep]
        if compat:
            args += ["--compat", compat]
        self.run(args)
        files = sorted(outdir.glob("render_tree_*.json"))
        return [json.loads(f.read_text(encoding="utf-8")) for f in files]

    def export_text(self, src: Path, outdir: Path) -> list[str]:
        """쪽마다 문서 본문 텍스트. 글자층 검사의 기준이 된다."""
        outdir.mkdir(parents=True, exist_ok=True)
        self.run(["export-text", str(src), "-o", str(outdir) + os.sep])
        return [f.read_text(encoding="utf-8", errors="replace")
                for f in sorted(outdir.glob("*.txt"))]

    def thumbnail(self, src: Path, dst: Path) -> bool:
        """한컴이 저장할 때 문서에 넣어 둔 1쪽 미리보기 그림. 없으면 False."""
        try:
            self.run(["thumbnail", str(src), "-o", str(dst), "--json"], timeout=120)
        except RhwpError:
            return False
        return dst.is_file() and dst.stat().st_size > 0


def warnings_of(text: str) -> list[str]:
    """rhwp 가 남긴 조판 경고. 글꼴 대체 안내처럼 늘 나오는 줄은 뺀다."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("{") or "fallback" in s or "--fallback" in s:
            continue
        if "빈칸으로 렌더링될 수 있습니다" in s or s.startswith("PDF 내보내기 완료"):
            continue
        if re.match(r"^[A-Z_]{6,}:", s):
            out.append(s)
    return out


def export_pdf_pages(rh: Rhwp, src: Path, workdir: Path, pages: int, workers: int,
                     compat: str | None = None) -> tuple[list[Path], list[str]]:
    """쪽마다 따로 내보낸다 — 여러 rhwp 를 동시에 돌려 큰 문서를 빨리 끝낸다.

    rhwp 의 PDF 생성은 쪽 수에 비례해 느려지고(실측 157쪽 53초) 한 프로세스는 코어 하나만
    쓴다. 문서를 읽고 조판하는 데는 0.3초도 안 걸리므로 쪽마다 프로세스를 띄워도 낭비가
    작다. 쪽별 결과는 같은 렌더러의 같은 조판이라 한 번에 내보낸 것과 그림이 같다.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    outs = [workdir / f"p{i + 1:05d}.pdf" for i in range(pages)]
    warns: list[str] = []

    def one(i):
        _, w = rh.export_pdf(src, outs[i], page=i, compat=compat)
        return w

    with ThreadPoolExecutor(max(1, workers)) as ex:
        for w in ex.map(one, range(pages)):
            warns += w
    return outs, sorted(set(warns))
