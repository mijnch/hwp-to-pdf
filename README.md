# hwp-to-pdf

> 한글 문서(.hwp / .hwpx)를 **한컴오피스 없이, 이 PC 안에서만** PDF 로 바꾸는 로컬 도구.
> 보기 좋은 PDF 에서 그치지 않고, **PDF 에서 뽑아낸 글자가 본문과 같도록** 글자층을 바로잡고,
> 변환이 원본과 얼마나 같은지 **스스로 재서** 다를 때 알려 준다.

| | |
|---|---|
| **입력 → 출력** | `.hwp`(HWP 5.0·일부 3.0) · `.hwpx` → PDF (글자 선택·검색 가능) |
| **동작 방식** | 전부 로컬. 그리기는 [rhwp](https://github.com/edwardkim/rhwp)(오픈소스 HWP 렌더러, MIT). 업로드 없음 |
| **글자층** | 실제 시험지 4개, 추출기 4종(pdfium·MuPDF·pdfminer·pypdf) 모두 **한글 글자 100%** — rhwp 그대로는 pdfium 60~100%, 띄어쓰기는 pdfminer 0% |
| **자가 검사** | 한컴이 문서에 넣어 둔 1쪽 미리보기와 대조 · 글자층 회수율 — 문턱 아래면 "확인 권장" |
| **속도** | 2~3쪽 시험지 약 2초 · 157쪽 문서 24초 (쪽마다 동시에 그린다) |
| **검증** | 단위 검증 62개 + 합성 문서 종단 검증 29항목(CI 31) + rhwp 공개 시험 문서 283개 |
| **설치** | `환경 설치.bat` 더블클릭 (Python 3.14 만 미리 있으면 된다) |

<details>
<summary><b>In English</b></summary>

A local, offline converter from Korean Hangul word-processor files (.hwp / .hwpx) to PDF that
does not need Hancom Office. Rendering is done by [rhwp](https://github.com/edwardkim/rhwp).

rhwp's PDFs look right but their **text layer is broken** for downstream readers: every glyph is
its own text object inside a page-sized Form XObject, so pdfium (Chrome/Edge, many pipelines) drops
repeated bold glyphs as "fake bold" duplicates (10–40 % of Hangul lost), and pdfminer/MuPDF/pypdf
lose word spacing because no space glyphs are drawn. This tool flattens the page form and inserts
invisible (Tr 3) spaces where rhwp's render tree says the original text has them. Rendering stays
the same (≤ 2/255 grey levels from float re-association); text extraction becomes 100 % for Hangul
across four extractors.

It also measures itself: Hancom Office stores a preview image of page 1 inside every document it
saves; the tool renders its own page 1 at that size and compares ink layouts tile-by-tile with
local alignment. Low agreement (< 0.75) — e.g. a column layout that collapsed — is reported.
</details>

## 어떻게 동작하나

```
.hwp/.hwpx ─┬─ rhwp info ──────────── 쪽 수·형식·마지막으로 저장한 한컴 세대 (암호 문서면 물어본다)
            ├─ rhwp export-pdf -p N ── 쪽마다 동시에 그린다 (8개까지) → 한 PDF 로 합친다
            ├─ rhwp export-render-tree  줄·낱말 묶음의 원문(공백 포함)과 위치
            │        └─▶ 글자층 보정: Form 펼치기 + 띄어쓰기 자리에 보이지 않는 공백
            ├─ rhwp export-text ─────── 본문 → 글자층 회수율 검사의 기준
            └─ rhwp thumbnail ───────── 한컴 1쪽 미리보기 → 1쪽 모양 대조
                                         └─▶ PDF 출력/  (+ 끝에 표식 한 줄)
```

## 설계에서 중요했던 판단

### 1. 엔진: rhwp — LibreOffice + H2Orestart 보다 확실히 낫다

한컴오피스가 없는 PC에서 쓸 수 있는 길은 둘이었다. 같은 실제 시험지 4개를 둘 다로 바꿔
**한컴이 직접 그린 1쪽 미리보기**와 맞대 보았다(아래 4절의 점수).

| | rhwp 0.8.7 | LibreOffice 26.8.1 + H2Orestart 0.7.14 |
|---|---|---|
| 1쪽 일치도 | **0.80 ~ 0.93** | 0.35 ~ 0.72 |
| 2단 시험지 | 단 배치 유지 | 오른쪽 단이 머리 표 위로 올라가고 2쪽 내용이 1쪽으로 넘침 |
| 수식 | 대부분 정상 | `Deg`·`CENTIGRADE`·`TIMES`·`prime` 같은 수식 명령어가 글자로 찍힘 |
| 크기 | 실행 파일 하나 31MB | LibreOffice 370MB + Java 49MB |

### 2. rhwp 의 PDF 는 보기엔 맞지만 글자를 뽑으면 깨진다 — 그래서 고친다

이 도구의 산출물은 사람만 보는 것이 아니다. Claude 같은 LLM, 검색, 복사, 그리고 PDF 를 다시
Markdown 으로 바꾸는 도구가 **PDF 의 글자층**을 읽는다. rhwp(svg 백엔드)는 쪽 전체를 1/971 배율의
Form XObject 하나로 감싸고 그 안에서 **글자를 한 자씩 따로** 그린다. 그 결과(실측):

- **pdfium**(Chrome·Edge 뷰어 등): 굵은 글씨가 무더기로 빠진다. rhwp 는 굵은 글자를 채움+윤곽선
  (Tr 2)으로 그리며 위치를 `cm` 으로 주고 글자 행렬은 모두 원점에 두는데, Form 안에서는 pdfium 이
  이것을 '같은 자리에 겹쳐 찍은 가짜 굵게' 로 보고 앞의 다섯 글자 안에 되풀이된 글자를 버린다.
  "2024학년도 제1학기" 가 "20 4학년도 제1 기" 가 된다.
- **pdfminer**: 띄어쓰기·줄바꿈이 하나도 없다. **MuPDF**·**pypdf**: 띄어쓰기 대부분이 사라진다
  (공백 글자를 아예 그리지 않으므로 글자 간격으로 추정해야 하는데 한글 띄어쓰기는 좁다).

고친 방법 (둘 다 그림은 그대로 — 아래 검증):

1. **쪽을 감싼 Form 을 쪽 본문으로 펼친다.** 원래의 두 변환 행렬을 그대로 차례로 걸고 BBox 클립을
   직접 건다. 이것만으로 pdfium 탈락과 pdfminer 의 줄·띄어쓰기 소실이 사라진다.
2. **보이지 않는 공백을 끼워 넣는다.** rhwp 렌더 트리에는 낱말 묶음마다 공백까지 포함한 원문과
   위치가 있다. 그려진 글자를 묶음에 짝짓고(실측 283개 문서에서 묶음 대부분이 1:1), 원문의 공백
   자리에 투명 글자(Tr 3, 표준 Helvetica)를 둔다. 묶음의 글자 수가 그려진 글자 수와 다르면
   (합자·채움 문자) 그 묶음은 건드리지 않는다.

**기각한 방법:** 글자 객체를 한 줄의 TJ 로 합치기 — 렌더링이 쪽당 1~2천 화소 바뀌었고 띄어쓰기는
여전히 추출기의 간격 추정에 맡겨야 했다. 줄마다 ActualText 로 감싸기 — pdfium 만 고쳐지고 MuPDF 는
글자를 중복시켰다. `--text-as-paths` + 숨은 글자층 — 정확하지만 파일이 5~7배(2쪽 2.3MB) 커졌다.

**실제 시험지 4개(대학물리·전기회로), 한글만 센 회수율** — 추출 순서는 따지지 않는다(단·표 순서는 추출기마다 다르다)

| 추출기 | rhwp 그대로: 글자 / 낱말 | 이 도구: 글자 / 낱말 |
|---|---|---|
| pdfium | 60.0~100 % / 27.3~66.7 % | **100 % / 92.6~100 %** |
| MuPDF | 100 % / 5.5~100 % | **100 % / 92.6~100 %** |
| pdfminer | 100 % / **0 %** | **100 % / 92.3~100 %** |
| pypdf | 100 % / 56.7~100 % | **100 % / 92.6~100 %** |

남은 낱말 차이는 수식에 붙은 낱말이다(기준 본문은 수식을 스크립트로, PDF 는 그려진 기호로 담는다).

### 3. 큰 문서는 쪽마다 동시에 그린다

rhwp 의 PDF 생성은 쪽 수에 비례해 느리고(157쪽 53초) 코어 하나만 쓴다. 문서를 읽고 조판하는 데는
0.3초도 안 걸리므로 쪽마다 rhwp 를 따로 띄워(최대 8개) 합친다. 쪽별 결과는 한 번에 내보낸 것과
**화소 단위로 같다**(종단 검증이 매번 확인). 157쪽 문서가 전체 처리 **24초**, 파일은 8.9 → 8.0MB.

### 4. 스스로 잰다 — 한컴 미리보기 대조

한컴오피스는 문서를 저장할 때 1쪽 그림(PrvImage, 약 724×1024)을 문서 안에 넣는다. 한컴이 직접 그린
것이므로 한컴 없는 PC 에서도 정답지가 된다. 우리 PDF 1쪽을 같은 크기로 그려 잉크 분포를 비교하되,
글꼴 대체와 몇 화소의 줄 밀림은 어쩔 수 없으므로 **작은 칸마다 ±16화소 안에서 가장 잘 맞는 자리**를
찾는다. 내용이 빠지거나 단이 무너지면 어느 자리에서도 맞지 않는다.

rhwp 공개 시험 문서 중 미리보기가 있는 232개: 중앙값 **0.94**, 0.75 미만 21개(9%). 0.75 아래를 직접
열어 보니 쪽 나눔 실패(1쪽 절반이 비고 본문이 2쪽으로 밀림), 배경 그림 누락, 다른 레이아웃 등 실제
차이였다 — 그래서 문턱이 **0.75** 다. 사용자 시험지 4개는 0.81~0.95.

글자층은 rhwp 가 문서에서 직접 뽑은 본문을 기준으로, 완성된 PDF 에서 pdfium 이 뽑은 한글의 비율을 잰다
(95% 미만이면 알림).

## 실측

이 PC: Ryzen AI 5 435 (6C/12T), RAM 16GB, Windows 11

| 항목 | 값 |
|---|---|
| 시험지 2~3쪽 | 1.6 ~ 2.7초 (자가 검사 포함) |
| 82쪽 / 157쪽 | 11.1초 / 23.9초 |
| rhwp 공개 시험 문서 283개(40쪽 이하) | 실패는 **암호 문서 2개뿐**(HWP 3.0 17개 포함 모두 변환) |
| 그중 한글 글자 회수율(pdfium) | 하위 10%: 0.84 → **1.00** |
| 그중 한글 낱말 회수율(pdfium) | 중앙값: 0.79 → **1.00** |

## 검증

```bash
engine\venv\Scripts\python engine\단위_검증.py
engine\venv\Scripts\python engine\종단_검증.py
```

**단위 검증 62개** — rhwp 없이 몇 초. 설정 읽기, 산출물 표식(우리 것 / 수정됨 / 남의 것), 출력 이름
충돌, 건너뛰기·재변환 판정, 암호 문서 질문, rhwp 오류 문구 정리, 그리고 rhwp 모양을 흉내 낸 PDF 로
글자층 보정(굵은 글씨·클립·밑줄 경계·글자 수 불일치 묶음, 공백 위치, q/Q 짝, 원래 그리기 명령 보존),
미리보기 대조(같은 그림·밀린 그림·무너진 단·빈 그림)를 확인한다.

**종단 검증 29항목** — 약 15초. rhwp scaffold 로 시험 문서를 그때그때 만들고(본문을 굵게 바꿔 pdfium
탈락을 재현한다) 도구를 끝까지 돌린다: 하위 폴더·같은 이름 두 형식·깨진 파일·다른 형식, 쪽 수,
글자층(rhwp 원본 대비), **그림이 그대로인지(화소 비교)**, 쪽별 동시 내보내기 = 한 번에 내보내기,
다시 실행 시 건너뛰기·원본이 바뀐 것만 재변환·수정된 PDF 보존·남의 PDF 보호, 끌어다 놓기(파일·폴더),
원본 옆 저장, 미리보기 대조(같은 그림 / 무너진 그림 / 없음). 사용자 문서는 쓰지 않는다.
CI 는 pypdf·pdfminer 를 더 깔아 그 둘로도 글자층을 뽑아 본다(31항목) — 실행 환경에는 넣지 않는다.

`환경 설치.bat` 도 끝에서 합성 문서 하나를 실제로 끝까지 변환해 본다 — import 가 된다고 도구가
도는 것은 아니다.

## 한계

- **그림은 rhwp 의 것이다.** 한컴과 똑같지 않다. 특히 이 PC 에 한컴 글꼴(함초롬바탕 등)이 없으면
  바탕·굴림 등으로 대체되어 글자 모양과 폭이 조금 다르다(줄 바꿈 위치는 문서에 저장된 대로 따른다).
- 실측에서 본 rhwp 의 차이: 수식의 **대문자 그리스 문자 명령**(`DELTA` → Δ 가 아니라 글자 "DELTA"),
  정의가 비어 있는 글머리표(●)가 그려지지 않음, 일부 문서의 쪽 나눔, 일부 배경 그림. 미리보기 대조가
  1쪽에서 이런 차이를 잡아 "확인 권장"으로 알리지만, **2쪽 이후는 재지 못한다.**
- 수식의 글자층은 그려진 기호의 순서다(분수의 분자·분모가 줄줄이 나온다). 수식의 뜻은 그림으로 본다.
- 암호 문서는 실행 창에서 암호를 묻는다(Enter 로 건너뜀). 바로가기 없이 다른 프로그램에서 부르면 건너뛴다.
- HWP 3.0 은 rhwp 가 "일부" 지원한다.

## 설치

**미리 있어야 하는 것**: Windows 10/11, [Python 3.14](https://www.python.org/downloads/)

`환경 설치.bat` 더블클릭. 한 번만 하면 된다.

- 폴더 안에 가상환경을 만들고 `engine/requirements.txt`(6개, 판 고정)를 설치한다
- rhwp 0.8.7 Windows 실행 파일을 GitHub 릴리스에서 받아 **지문(SHA-256)을 대조**한 뒤 `engine/rhwp/`에 둔다
- 합성 문서 하나를 끝까지 변환해 보고 결과를 알려 준다

**실행**

```
HWP 입력/     ← 한글 문서를 넣는다 (하위 폴더 가능)
```

`HWP to PDF 실행.bat` 더블클릭 → `PDF 출력/`에 결과. 바로가기나 `HWP to PDF 실행.bat`에 파일·폴더를
**끌어다 놓으면** 그것만 바로 처리한다. 설정은 [`설정.ini`](설정.ini), 자세한 사용법은 [사용법.md](사용법.md),
결과 PDF 를 LLM 에 넣을 때의 안내는 [CLAUDE.md](CLAUDE.md).

## 구조

```
HWP to PDF 실행.bat     실행 진입점 (파일·폴더를 끌어다 놓아도 된다)
환경 설치.bat           최초 1회 설치
설정.ini                사용자 설정 (저장 위치 · 미리보기 대조)
engine/hwp2pdf.py       흐름 (설정·대상·건너뛰기·변환·기록)
engine/rhwp_cli.py      rhwp 호출 (쪽별 동시 내보내기, 오류 문구)
engine/pdf_text.py      글자층 보정 (Form 펼치기 · 보이지 않는 공백)
engine/fidelity.py      자가 검사 (한컴 미리보기 대조 · 글자층 회수율)
engine/단위_검증.py      단위 검증 62개
engine/종단_검증.py      합성 문서로 끝까지 돌려 채점
engine/setup_env.py     가상환경 · 패키지 · rhwp(지문 대조) · 실제 변환 시험
engine/requirements.txt 판 고정
```

**폴더 밖을 더럽히지 않는다.** 가상환경·rhwp·임시 파일이 전부 도구 폴더 안에 생긴다.

산출물 PDF 맨 끝(`%%EOF` 뒤)에는 `%HWP2PDF {…}` 한 줄이 붙는다 — 원본의 크기·지문, 엔진 판, 그 앞
내용의 지문. 이것으로 "이미 변환됨 / 원본이 바뀜 / 변환 뒤 누가 고침(보존) / 남이 만든 같은 이름 PDF(보호)"를 가린다.

## 라이선스

MIT. 변환 엔진 rhwp 는 MIT(© Edward Kim), 받아 쓰는 라이브러리는 pikepdf(MPL-2.0),
pypdfium2(Apache-2.0/BSD-3), NumPy(BSD-3), Pillow(MIT-CMU), lxml(BSD-3).
"한글", "한컴", "HWP", "HWPX" 는 한글과컴퓨터의 상표이며 이 도구는 한글과컴퓨터와 관계가 없다.
