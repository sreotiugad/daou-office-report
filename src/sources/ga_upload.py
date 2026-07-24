"""
GA 원본(GA_DO / GA_HR) 업로드 파서.

GA4 API 연동 전(또는 확인용)에 GA 데이터를 파일로 넣기 위한 경로.
GAS 원본이 GA_DO/GA_HR 시트에서 읽던 것과 동일한 레코드를 만든다.

헤더 이름을 후보 목록으로 자동 인식하며, 헤더 행 위치도 자동 탐지한다.

반환: (records, logs)
 record = {date, medium, campaign, content, conv, emp}
"""
import pandas as pd

from ..helpers import to_num, to_str, normalize_date

# GA4 내보내기(한글/영문)에서 자주 쓰이는 헤더 후보들
_GA_CANDS = {
    "date":     ["날짜", "일", "date", "일자"],
    "medium":   ["세션 소스/매체", "소스/매체", "세션 소스 / 매체", "세션소스/매체",
                 "session source / medium", "sessionsourcemedium", "소스 / 매체"],
    "campaign": ["세션 캠페인", "세션 캠페인 이름", "캠페인", "캠페인 이름",
                 "session campaign", "sessioncampaignname", "세션 캠페인명"],
    "content":  ["세션 수동 광고 콘텐츠", "수동 광고 콘텐츠", "광고 콘텐츠", "콘텐츠",
                 "session manual ad content", "sessionmanualadcontent", "광고콘텐츠"],
    "conv":     ["전환", "전환수", "세션 전환", "주요 이벤트", "핵심 이벤트", "이벤트 수",
                 "conversions", "가입", "sign_up", "key events", "전환 수"],
    "emp":      ["직원수", "직원 수", "employees", "employee", "직원"],
}

_FIELDS = ["date", "medium", "campaign", "content", "conv", "emp"]


def _read_any(file):
    name = getattr(file, "name", str(file)).lower()
    if name.endswith((".csv", ".tsv", ".txt")):
        return _read_csv_ragged(file)
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return pd.read_excel(file, header=None, dtype=str)
    # 확장자가 불명확하면 내용으로 판별: xlsx는 ZIP(PK)로 시작, 그 외엔 텍스트
    try:
        if hasattr(file, "seek"):
            file.seek(0)
        head = file.read(4)
        if hasattr(file, "seek"):
            file.seek(0)
        if isinstance(head, bytes) and head[:2] == b"PK":
            return pd.read_excel(file, header=None, dtype=str)
    except Exception:
        pass
    return _read_csv_ragged(file)


def _read_csv_ragged(file):
    """칸 수가 들쭉날쭉한 CSV(제목/기간 안내행 포함)를 안전하게 읽는다."""
    import csv
    import io as _io
    if hasattr(file, "seek"):
        file.seek(0)
    data = file.read()
    if isinstance(data, bytes):
        for enc in ("utf-8-sig", "utf-8", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = data.decode("utf-8", errors="replace")
    else:
        text = data
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(_io.StringIO(text), delimiter=delim))
    if not rows:
        return pd.DataFrame()
    maxw = max(len(r) for r in rows)
    rows = [r + [""] * (maxw - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=str).fillna("")


def _norm(s):
    return str(s).replace(" ", "").lower()


def _detect_header_row(raw):
    wanted = {_norm(c) for cands in _GA_CANDS.values() for c in cands}
    best_row, best_hits = 0, -1
    scan = min(len(raw), 20)
    for i in range(scan):
        cells = [_norm(x) for x in raw.iloc[i].tolist() if to_str(x)]
        hits = sum(1 for c in cells if c in wanted)
        if hits > best_hits:
            best_hits, best_row = hits, i
    return best_row, best_hits


def _match_columns(header_cells):
    norm = [_norm(x) for x in header_cells]
    mapping = {}
    for f in _FIELDS:
        idx = None
        for cand in _GA_CANDS[f]:
            key = _norm(cand)
            for j, h in enumerate(norm):
                if h == key:
                    idx = j
                    break
            if idx is not None:
                break
        mapping[f] = idx
    return mapping


def parse_ga_upload(file, source, logs=None):
    """GA 원본 엑셀/CSV → GA record 리스트. source 는 GA_SOURCES 항목(brand 등)."""
    if logs is None:
        logs = []
    if file is None:
        return [], logs
    sheet = source.get("sheet", "GA")
    try:
        raw = _read_any(file)
    except Exception as e:
        logs.append(f"❌ [{sheet}] 파일 읽기 실패: {e}")
        return [], logs

    if raw.empty:
        logs.append(f"⚠️ [{sheet}] 파일이 비어 있음")
        return [], logs

    hrow, _ = _detect_header_row(raw)
    header = raw.iloc[hrow].tolist()
    colmap = _match_columns(header)

    missing = [f for f in ("date", "medium", "conv") if colmap.get(f) is None]
    if missing:
        logs.append(f"❌ [{sheet}] 필수 열({missing})을 찾지 못함. 헤더행={hrow} 헤더={header}")
        return [], logs

    def cell(row, field):
        idx = colmap.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    records = []
    for _, r in raw.iloc[hrow + 1:].iterrows():
        row = r.tolist()
        ymd = normalize_date(cell(row, "date"))
        medium = to_str(cell(row, "medium"))
        conv = to_num(cell(row, "conv"))
        emp = to_num(cell(row, "emp"))
        # GAS 원본과 동일하게: 완전 빈 행만 건너뛴다.
        # (GAS는 'Grand total' 합계행도 포함해 원본 전환을 집계하며,
        #  이 행은 소스/매체가 비어 화이트리스트에서 걸러져 최종에는 영향 없음)
        if not ymd and not medium and conv == 0 and emp == 0:
            continue
        records.append({
            "date": ymd,
            "medium": medium,
            "campaign": to_str(cell(row, "campaign")),
            "content": to_str(cell(row, "content")),
            "conv": conv,
            "emp": emp,
        })
    logs.append(f"✅ [{sheet}] GA 업로드 파싱 완료: {len(records)}행 (헤더행={hrow})")
    return records, logs
