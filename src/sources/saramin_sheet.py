"""
사람인 — 메일 자동 적재 탭 [saramin_raw] 읽기.

gas/saramin_to_sheet.gs 가 Saramin AdsCenter 보고서 메일의 첨부(xlsx)에서
일별 행만 뽑아 매핑 구글시트의 [saramin_raw] 탭에 쌓아 둔다.
(일자·캠페인·광고그룹·광고) 중복은 최신 보고서 값으로 이미 정리돼 있다.

업로드 파일이 없을 때 이 탭을 사람인 원본으로 쓴다.

반환: (records, logs, info)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view}
 info   = {"found": bool, "maxDate": 탭의 마지막 일자, "lastReport": 최근 보고서일,
           "missing": 선택 기간 중 데이터가 없는 날짜 목록}
"""
from datetime import date, timedelta

from ..helpers import to_num, to_str, normalize_date

TAB = "saramin_raw"


def is_configured(secrets):
    from .. import sheets_store
    return sheets_store.is_configured(secrets)


def _days(since, until):
    d = date.fromisoformat(since)
    e = date.fromisoformat(until)
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def get_saramin_sheet_rows(since, until, secrets, logs=None):
    if logs is None:
        logs = []
    info = {"found": False, "maxDate": "", "lastReport": "", "missing": []}
    if not is_configured(secrets):
        return [], logs, info
    from .. import sheets_store
    try:
        vals = sheets_store.read_values(TAB, secrets)
    except Exception as e:
        logs.append(f"❌ [사람인] 메일 적재 탭 읽기 실패: {e}")
        return [], logs, info
    if not vals or len(vals) < 2:
        logs.append("⚠️ [사람인] 메일 적재 탭(saramin_raw)이 없거나 비어 있음 — 업로드 필요")
        return [], logs, info

    header = [to_str(h) for h in vals[0]]

    def col(name):
        return header.index(name) if name in header else None

    c = {k: col(v) for k, v in {
        "date": "일자", "campaign": "캠페인명", "adGroup": "광고그룹명", "adName": "광고명",
        "imp": "노출수", "click": "클릭수", "report": "보고서일"}.items()}
    if c["date"] is None or c["campaign"] is None:
        logs.append(f"❌ [사람인] saramin_raw 헤더 이상: {header}")
        return [], logs, info

    def cell(row, k):
        i = c.get(k)
        return row[i] if i is not None and i < len(row) else ""

    records, all_dates, reports = [], set(), set()
    for row in vals[1:]:
        ymd = normalize_date(cell(row, "date"))
        if not ymd:
            continue
        all_dates.add(ymd)
        if to_str(cell(row, "report")):
            reports.add(to_str(cell(row, "report")))
        records.append({
            "device_raw": "",
            "date": ymd,
            "campaign": to_str(cell(row, "campaign")),
            "adGroup": to_str(cell(row, "adGroup")),
            "adName": to_str(cell(row, "adName")),
            "imp": to_num(cell(row, "imp")),
            "click": to_num(cell(row, "click")),
            "cost": 0, "rank": 0, "view": 0,
        })

    info["found"] = True
    info["maxDate"] = max(all_dates) if all_dates else ""
    info["lastReport"] = max(reports) if reports else ""
    if since and until:
        info["missing"] = [d for d in _days(since, until) if d not in all_dates]
    msg = f"[사람인] 메일 자동 적재 탭 사용: 전체 {len(records)}행 (데이터 ~{info['maxDate']}, 최근 보고서 {info['lastReport']})"
    if info["missing"]:
        msg += f" · ⚠️ 선택 기간 중 데이터 없는 날 {len(info['missing'])}일: {', '.join(info['missing'])}"
    logs.append(msg)
    return records, logs, info
