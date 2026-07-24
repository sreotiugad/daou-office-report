"""
업로드 원본(네이버·사람인) 파서.

엑셀 헤더가 매번 조금씩 달라서, config 의 col 후보 목록으로 헤더를 찾아 매핑한다.
헤더 행 위치도 파일마다 달라서, 후보 헤더가 가장 많이 매칭되는 행을 헤더로 자동 탐지한다.

반환: (records, logs)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view}
"""
import pandas as pd

from ..helpers import to_num, to_str, normalize_date

_FIELDS = ["device", "date", "campaign", "adGroup", "adName",
           "imp", "click", "cost", "rank", "view"]


def _read_any(file):
    """엑셀/CSV 를 헤더 없이 원시 2차원으로 읽는다.
    CSV는 줄마다 칸 수가 달라도(제목행/합계행 등) 안전하게 읽는다."""
    name = getattr(file, "name", str(file)).lower()
    if name.endswith((".csv", ".tsv", ".txt")):
        return _read_csv_ragged(file)
    return pd.read_excel(file, header=None, dtype=str)


def _read_csv_ragged(file):
    """칸 수가 들쭉날쭉한 CSV를 csv 모듈로 안전하게 읽어 DataFrame 반환."""
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


def _candidates(col_spec, field):
    v = col_spec.get(field)
    if not v:
        return []
    return [str(x).strip() for x in v]


def _detect_header_row(raw, col_spec):
    """후보 헤더가 가장 많이 매칭되는 행 인덱스를 반환."""
    wanted = set()
    for f in _FIELDS:
        for c in _candidates(col_spec, f):
            wanted.add(c.replace(" ", "").lower())
    best_row, best_hits = 0, -1
    scan = min(len(raw), 15)
    for i in range(scan):
        cells = [str(x).replace(" ", "").lower() for x in raw.iloc[i].tolist() if to_str(x)]
        hits = sum(1 for c in cells if c in wanted)
        if hits > best_hits:
            best_hits, best_row = hits, i
    return best_row


def _match_columns(header_cells, col_spec):
    """field -> 실제 열 인덱스. 못 찾으면 None."""
    norm = [str(x).replace(" ", "").lower() for x in header_cells]
    mapping = {}
    for f in _FIELDS:
        idx = None
        for cand in _candidates(col_spec, f):
            key = cand.replace(" ", "").lower()
            for j, h in enumerate(norm):
                if h == key:
                    idx = j
                    break
            if idx is not None:
                break
        mapping[f] = idx
    return mapping


def parse_upload(file, col_spec, logs=None):
    if logs is None:
        logs = []
    if file is None:
        return [], logs
    try:
        raw = _read_any(file)
    except Exception as e:
        logs.append(f"❌ 업로드 파일 읽기 실패: {e}")
        return [], logs

    if raw.empty:
        logs.append("⚠️ 업로드 파일이 비어 있음")
        return [], logs

    hrow = _detect_header_row(raw, col_spec)
    header = raw.iloc[hrow].tolist()
    colmap = _match_columns(header, col_spec)

    missing = [f for f in ("date", "campaign") if colmap.get(f) is None]
    if missing:
        logs.append(f"❌ 필수 열({missing})을 찾지 못함. 헤더행={hrow} 헤더={header}")
        return [], logs

    def cell(row, field):
        idx = colmap.get(field)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    records = []
    for _, r in raw.iloc[hrow + 1:].iterrows():
        row = r.tolist()
        campaign = to_str(cell(row, "campaign"))
        ymd = normalize_date(cell(row, "date"))
        if not campaign and not ymd:
            continue
        records.append({
            "device_raw": to_str(cell(row, "device")),
            "date": ymd,
            "campaign": campaign,
            "adGroup": to_str(cell(row, "adGroup")),
            "adName": to_str(cell(row, "adName")),
            "imp": to_num(cell(row, "imp")),
            "click": to_num(cell(row, "click")),
            "cost": to_num(cell(row, "cost")),
            "rank": to_num(cell(row, "rank")),
            "view": to_num(cell(row, "view")),
        })
    logs.append(f"✅ 업로드 파싱 완료: {len(records)}행 (헤더행={hrow})")
    return records, logs
