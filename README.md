# hwp-to-pdf

> 한글 문서(.hwp / .hwpx)를 **한컴오피스 없이, 이 PC 안에서만** PDF 로 바꾸는 로컬 도구.
> 보기 좋은 PDF 에서 그치지 않고, **PDF 에서 뽑아낸 글자가 본문과 같도록** 글자층을 바로잡고,
> 변환이 원본과 얼마나 같은지 **스스로 재서** 다를 때 알려 준다.

| | |
|---|---|
| **입력 → 출력** | `.hwp`(HWP 5.0·일부 3.0) · `.hwpx` → PDF (글자 선택·검색 가능) |
| **동작 방식** | 전부 로컬. 그리기는 [rhwp](https://github.com/edwardkim/rhwp)(오픈소스 HWP 렌더러, MIT). 업로드 없음 |
| **글자층** | 실제 시험지 4개, 추출기 4종(pdfium·MuPDF·pdfminer·pypdf) 모두 **한글 글자 100%** — rhwp 그대로는 pdfium 60~100%, 띄어쓰기는 pdfminer 0% |
| **원본 일치** | rhwp 를 한컴 원본과 맞대 8곳 고친 판을 쓴다 — 실제 시험지 4개의 한컴 1쪽 실루엣 일치율 **80 → 98.6%** |
| **자가 검사** | 한컴이 문서에 넣어 둔 1쪽 미리보기와 대조 · 글자층 회수율 — 문턱 아래면 "확인 권장" |
| **속도** | 2~3쪽 시험지 약 2초 · 157쪽 문서 24초 (쪽마다 동시에 그린다) |
| **검증** | 단위 검증 90개 + 합성 문서 종단 검증 30항목(CI 31) + rhwp 공개 시험 문서 312개 + 한컴 PDF 34쌍 615쪽 |
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
local alignment. Low agreement — e.g. a column layout that collapsed — is reported.

To push the match further, converted pages were compared against Hancom's own renderings (the
page-1 previews of four real exam papers, and 34 Hancom-exported PDFs / 615 pages from the rhwp
repository) with rhwp's own *visual sweep* silhouette metric. Fonts and compatibility options moved
nothing. Hancom's previews turned out to be drawn 0.58 % smaller in both directions, which the
comparison now corrects for. Tracing each remaining mismatching band to its cause led to eight
layout fixes in rhwp (zone start after a single-column header in two-column exam layouts, default
bullet, all-caps Greek equation commands, picture+caption centring in table cells, inline one-row
table height from the stored line ladder, double first-line indent and lost centring on
equation-only lines, lost hanging indent beside a square-wrapped figure). Page-1 silhouette
agreement on the exam papers went from 80 % to 98.6 % — what remains is mostly font substitution —
with no change on the 615 reference pages and all of rhwp's library tests passing.
`engine/build_rhwp.py` rebuilds the patched engine from source; `engine/원본_대조.py` repeats the
measurement.
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
찾는다. 내용이 빠지거나 단이 무너지면 어느 자리에서도 맞지 않는다. 한컴 미리보기는 가로세로 0.58% 작게
그려져 있어 우리 그림도 같은 비율로 줄여 맞댄다(5절).

rhwp 공개 시험 문서 312개 중 미리보기가 있는 256개(고친 엔진): 중앙값 **0.94**, 0.75 미만 29개(11%).
0.75 아래를 직접 열어 보니 쪽 나눔 실패(1쪽 절반이 비고 본문이 2쪽으로 밀림), 배경 그림 누락, 다른
레이아웃 등 실제 차이였다 — 그래서 문턱이 **0.75** 다. 사용자 시험지 4개는 0.87~0.97.
옛 한컴이 넣은 작은 미리보기(177×250, 공개 문서의 절반)에서는 한컴 글자가 진한 검정이고 우리 글자는
흐린 회색이라, 칸 크기를 해상도에 맞춰 줄이면 같은 배치도 점수가 내려간다 — 그래서 크기와 상관없이
반으로 줄여 같은 칸으로 비교한다. 실루엣 일치율도 이 크기에서는 의미가 없어 재지 않는다.

글자층은 rhwp 가 문서에서 직접 뽑은 본문을 기준으로, 완성된 PDF 에서 pdfium 이 뽑은 한글의 비율을 잰다
(95% 미만이면 알림).

### 5. 원본과 맞대 rhwp 를 고친다

"일치율을 최대로" 올리려면 먼저 잴 정답지가 있어야 한다. 두 가지를 쓴다.

- **한컴 1쪽 미리보기** — 사용자의 실제 시험지 4개(2단 시험지·기출문제지). 한컴이 그린 그림이다.
- **한컴이 내보낸 PDF** — rhwp 저장소에는 원본 문서와 한컴오피스 2018~2024 가 직접 내보낸 PDF 가 함께
  있는 쌍이 34개(615쪽) 있다(`engine/원본대조_목록.json`, 커밋 고정·git 지문 대조).

지표는 rhwp 개발진의 Visual Sweep 과 같은 **실루엣 일치율** — 96dpi 에서 내용이 있는 화소 가운데 2px 안에
상대 쪽 내용이 있는 비율(`engine/fidelity.py` `silhouette`). 칸 단위 정렬이 없어 몇 화소 밀림도 깎인다.

**효과가 없던 것 (한컴 PDF 615쪽 평균 88.11 기준):** rhwp 의 조판 세대 옵션(2024)·인쇄 모드 88.11,
함초롬 자리에 이름을 바꾼 Noto·나눔·HY 글꼴 88.10~88.11. 차이는 글꼴이 아니라 **조판 위치**였다.

**발견 — 한컴 미리보기는 가로세로 0.58% 작게 그려져 있다.** 정밀한 한컴 PDF 와 맞대면 쪽 전체에서
어긋남이 0±2px 인데, 미리보기와는 아래·오른쪽으로 갈수록 비례해 벌어진다(세로: 사용자 문서 3개·126개
띠의 회귀 dy = 0.07 + 0.00581·y, 가로: 문서 4개 모두 왼쪽 단 +1px·오른쪽 단 +3px). 미리보기와 맞댈
때는 우리 그림도 같은 비율로 줄인다. 이것만으로 남은 차이의 절반가량이 측정 착시였음이 드러났다.

그다음 어긋난 띠를 하나씩 rhwp 소스의 원인까지 따라가 여덟 곳을 고쳤다
([engine/rhwp_patches/README.md](engine/rhwp_patches/README.md)):

| 고침 | 증상 |
|---|---|
| 다단 구역 시작 위치 (가장 큼) | 1단 안내 → 2단 본문 시험지에서 2단 본문 전체가 5~10px 아래로, 머리 표 높이가 한 번 더 더해져 148px 아래로 |
| 정의 없는 글머리표 | 한/글이 그리는 기본 ● 가 빠짐 |
| 대문자 그리스 문자 수식 명령 | `DELTA T` 가 ΔT 가 아니라 글자 "DELTA T" |
| 표 칸 안 그림 + 캡션 | 그림만 가운데에 둬 16px 아래 |
| 인라인 1행 표의 행 높이 | 행이 줄높이 합으로 2.6px 부풀어 칸 글자·표 아래 전체가 밀림 (저장 줄 사슬과 표 높이가 함께 증언할 때만 고침) |
| 수식만 있는 줄의 들여쓰기 | 첫 줄 들여쓰기가 두 번 들어가 수식이 12~33px 오른쪽으로 |
| 어울림 그림 옆 줄의 내어쓰기 | 그림 옆 줄들만 내어쓰기(20px)가 빠짐 |
| 가운데 정렬 문단의 수식 줄 | 가운데가 아니라 단 왼끝에 붙음(40px) |

| 정답지 | rhwp 0.8.7 릴리스 | 고친 판 |
|---|---:|---:|
| 실제 시험지 4개, 1쪽 (한컴 미리보기) | 72.7 · 82.6 · 99.7 · 64.7 → 평균 **79.9** | 98.0 · 99.0 · 99.7 · 97.8 → 평균 **98.6** |
| 한컴 PDF 34쌍 615쪽 | 평균 88.11 · 중앙 98.40 · 90% 이상 75.4% | 평균 88.14 · 중앙 98.40 · 90% 이상 75.6% |
| rhwp 자체 시험 | — | 라이브러리 3,870개 + 크레이트 182개 통과, 통합 6,251개는 고치기 전과 실패 목록이 같음(의도한 골든 1건 갱신) |
| rhwp 시험 문서 1,223개 (고치기 전·후 렌더 트리) | — | 10개만 달라짐, 한컴 PDF 가 있는 쪽은 모두 같거나 나아짐 |

시험지에 남은 1~2% 는 거의 글꼴이다(한컴 글꼴이 없어 대체 글꼴의 글자 모양이 다르다). 한컴 PDF 쪽에서
크게 오르지 않은 것은 그 손실의 대부분이 긴 문서의 쪽 나눔 차이라서다(86쪽짜리 한 문서가 손실의 절반 —
여러 쪽에 걸친 표를 rhwp 가 한 줄씩 더 담는다. rhwp 가 진행 중인 #1891 영역이라 손대지 않았다).
고친 곳에서 나빠진 쪽은 없다(쪽마다 비교).

고친 판은 rhwp 릴리스에 없으므로 `engine/build_rhwp.py` 로 소스에서 만들어 **이 저장소의 릴리스 파일**
([engine-21ed640-p5092a3](https://github.com/mijnch/hwp-to-pdf/releases/tag/engine-21ed640-p5092a3))로
올려 두었고, `환경 설치.bat` 이 그것을 받는다(아래 「설치」). 도구는
`engine/rhwp/BUILD.json` 으로 고친 판을 알아보고 판 표기를 `0.8.7+21ed640.p5092a3` 처럼 바꾼다 —
엔진이 바뀌었으므로 이미 만든 PDF 도 다음 실행에서 새로 만든다.

**다시 재기:** `engine\venv\Scripts\python engine\원본_대조.py` 는 `HWP 입력` 의 문서(또는 준 파일·폴더)를
한컴 미리보기와, `--쌍 <폴더>` 는 같은 이름의 (문서, 한컴 PDF) 쌍을 모든 쪽, `--rhwp-시험` 은 위 34쌍을
받아 잰다. `--차이` 를 주면 90% 미만 쪽의 겹친 그림(빨강 = 우리만, 파랑 = 원본만)을 `원본대조\차이\` 에 남긴다.

**기준선:** 엔진을 고칠 때마다 같은 문서로 다시 재어 앞의 점수와 비교한다.
`--기준 추가 <파일...>` 로 등록하고, `--기준` 으로 다시 재면 나빠진 문서를 짚고 종료 코드 1 을 낸다
(실루엣 0.3%p·칸 정렬 0.01·글자층 0.5% 넘게 하락, 쪽 수 변화). 의도한 변화면 `--기준 갱신`.
기록(`원본대조\기준.json`)에는 문서 경로·지문·점수만 남는다 — 문서는 복사하지 않고, 이 폴더는 커밋되지 않는다.
이 PC 에는 실제 시험지 4개를 등록해 두었다. 고치기 전 릴리스 엔진을 넣어 보면 4개 중 3개가 실루엣 98.0 → 72.7 처럼
나빠짐으로 잡힌다(나머지 하나는 고침 8건과 무관한 문서라 그대로).

## 실측

이 PC: Ryzen AI 5 435 (6C/12T), RAM 16GB, Windows 11

| 항목 | 값 |
|---|---|
| 시험지 2~3쪽 | 1.6 ~ 2.7초 (자가 검사 포함) |
| 82쪽 / 157쪽 | 11.1초 / 23.9초 |
| rhwp 공개 시험 문서 283개(40쪽 이하) | 실패는 **암호 문서 2개뿐**(HWP 3.0 17개 포함 모두 변환) |
| 그중 한글 글자 회수율(pdfium) | 하위 10%: 0.84 → **1.00** |
| 그중 한글 낱말 회수율(pdfium) | 중앙값: 0.79 → **1.00** |
| 실제 시험지 4개, 1쪽 실루엣 일치율 (한컴 미리보기) | 릴리스 79.9% → 고친 판 **98.6%** |
| 한컴 PDF 34쌍 615쪽 실루엣 일치율 | 88.1% (쪽 중앙값 98.4%) |

## 검증

```bash
engine\venv\Scripts\python engine\단위_검증.py
engine\venv\Scripts\python engine\종단_검증.py
```

**단위 검증 90개** — rhwp 없이 몇 초. 설정 읽기, 산출물 표식(우리 것 / 수정됨 / 남의 것), 출력 이름
충돌, 건너뛰기·재변환 판정, 암호 문서 질문, rhwp 오류 문구 정리, 그리고 rhwp 모양을 흉내 낸 PDF 로
글자층 보정(굵은 글씨·클립·밑줄 경계·글자 수 불일치 묶음, 공백 위치, q/Q 짝, 원래 그리기 명령 보존),
미리보기 대조(같은 그림·밀린 그림·무너진 단·빈 그림, 한컴식 세로 눌림), 실루엣 일치율(2px 관용·문턱
경계 색), 고친 rhwp 판 알아보기·설치 압축 풀기, 바탕 없는 PC 의 대체 글꼴 인자, 원본 대조의 쌍 찾기·지문·
기준선 비교(문턱 안의 흔들림은 넘기고 하락·쪽 수 변화·원본 교체는 잡는지)를 확인한다.

**종단 검증 30항목** — 약 15초. rhwp scaffold 로 시험 문서를 그때그때 만들고(본문을 굵게 바꿔 pdfium
탈락을 재현한다) 도구를 끝까지 돌린다: 하위 폴더·같은 이름 두 형식·깨진 파일·다른 형식, 쪽 수,
글자층(rhwp 원본 대비), **그림이 그대로인지(화소 비교)**, 쪽별 동시 내보내기 = 한 번에 내보내기,
다시 실행 시 건너뛰기·원본이 바뀐 것만 재변환·수정된 PDF 보존·남의 PDF 보호, 끌어다 놓기(파일·폴더),
원본 옆 저장, 미리보기 대조(같은 그림 / 무너진 그림 / 없음), 같은 원본을 두 번 변환하면 바이트로 같은 PDF.
사용자 문서는 쓰지 않는다.
CI 는 pypdf·pdfminer 를 더 깔아 그 둘로도 글자층을 뽑아 본다 — 실행 환경에는 넣지 않는다. 러너에는 바탕이
없어 명조를 맑은 고딕으로 그리는데, 그때는 rhwp 가 굵은 글씨를 빠뜨리지 않아 「탈락을 메움」 한 항목이 빠진다(31항목).

`환경 설치.bat` 도 끝에서 합성 문서 하나를 실제로 끝까지 변환해 본다 — import 가 된다고 도구가
도는 것은 아니다.

## 한계

- **그림은 rhwp 의 것이다.** 한컴과 똑같지 않다. 특히 이 PC 에 한컴 글꼴(함초롬바탕 등)이 없으면
  바탕·굴림 등으로 대체되어 글자 모양과 폭이 조금 다르다(줄 바꿈 위치는 문서에 저장된 대로 따른다).
  rhwp 는 PDF 에서 명조 계열을 **바탕** 하나로 대신 그리는데, 바탕이 없으면 경고만 내고 그 글자를 빈칸으로
  둔다(실측: GitHub Windows 러너 — 글자층 0%). 그래서 바탕·맑은 고딕 중 없는 쪽은 있는 쪽으로 대신하게
  하고(`--fallback-serif/--fallback-sans`) 실행 창에 알린다. 이 사실은 산출물 지문에 들어가 글꼴을
  설치하면 다시 만든다.
- 고친 판에도 남은 차이: 여러 쪽에 걸친 긴 표의 쪽 나눔, 한글이 든 수식의 폭, 쪽 기준 표 뒤 문단 간격,
  일부 배경 그림. 미리보기 대조가 1쪽에서 큰 차이를 잡아 "확인 권장"으로 알리지만,
  **2쪽 이후는 재지 못한다.** 릴리스 판을 쓰면 위 「5」의 여덟 가지 차이도 그대로 있다.
- 수식의 글자층은 그려진 기호의 순서다(분수의 분자·분모가 줄줄이 나온다). 수식의 뜻은 그림으로 본다.
- 암호 문서는 실행 창에서 암호를 묻는다(Enter 로 건너뜀). 바로가기 없이 다른 프로그램에서 부르면 건너뛴다.
- HWP 3.0 은 rhwp 가 "일부" 지원한다.

## 설치

**미리 있어야 하는 것**: Windows 10/11, [Python 3.14](https://www.python.org/downloads/)

`환경 설치.bat` 더블클릭. 한 번만 하면 된다.

- 폴더 안에 가상환경을 만들고 `engine/requirements.txt`(6개, 판 고정)를 설치한다
- **고친 rhwp**(이 저장소 릴리스 파일, 약 12MB)를 받아 **지문(SHA-256)을 대조**한 뒤 `engine/rhwp/`에 둔다.
  같은 고친 판이 이미 있으면 그대로 두고, 받지 못하면 rhwp 원래 릴리스 0.8.7 을 받는다(한컴 일치율이 낮다고 알린다)
- 합성 문서 하나를 끝까지 변환해 보고 결과를 알려 준다

확인한 것: 빈 폴더에 이 저장소를 새로 받아 위 절차대로 설치(가상환경·패키지·엔진 모두 새로 받음)하면,
원래 설치와 엔진 실행 파일이 같고(SHA-256) 실제 시험지 4개의 PDF 가 **바이트까지 같다**(끝의 표식 줄 제외 —
표식에는 만든 시각이 들어간다). 같은 PC 에서 한 확인이다. 다른 기계에서는 CI(GitHub 의 Windows 러너)가
매번 고친 엔진을 이 릴리스에서 받아 종단 검증을 돌린다 — 다만 러너에는 바탕 글꼴이 없어 명조가 맑은 고딕으로
그려지므로, 깔린 글꼴이 다른 PC 의 PDF 는 바이트로 같지 않다.

**고친 rhwp 를 직접 만들기 (선택, 15~30분):** `engine\venv\Scripts\python engine\build_rhwp.py`.
빌드 폴더(`engine\.build\`, 도구 경로에 빈칸이 있으면 `%LOCALAPPDATA%\hwp2pdf-build\` — MinGW gcc 는
설치 경로에 빈칸이 있으면 링크하지 못한다) 안에 Rust 1.93.1·MinGW-w64(지문 대조)를 받고, rhwp 소스를 고정 커밋에서 받아 고침을 넣고
빌드한 뒤, 합성 문서 변환을 통과해야 `engine\rhwp\rhwp.exe` 를 바꾼다(이전 것은 `rhwp.exe.prev`). git·인터넷·
디스크 약 5GB 가 필요하고, 끝나면 빌드 폴더를 지운다(`--keep` 이면 남긴다). 시스템 PATH 는 건드리지 않는다.

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
engine/fidelity.py      자가 검사 (한컴 미리보기 대조 · 글자층 회수율 · 실루엣 일치율)
engine/원본_대조.py      한컴 미리보기·한컴 PDF 와 맞대 일치율을 잰다 (엔진을 고칠 때의 잣대)
engine/원본대조_목록.json rhwp 저장소의 한컴 PDF 34쌍 (커밋·git 지문 고정)
engine/build_rhwp.py    고친 rhwp 를 소스에서 빌드해 설치
engine/rhwp_patches/    rhwp 고침과 근거
engine/단위_검증.py      단위 검증 90개
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
