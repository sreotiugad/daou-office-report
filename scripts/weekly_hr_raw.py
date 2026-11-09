# -*- coding: utf-8 -*-
"""
다우오피스HR 주간 보고서 적재 — 공유드라이브의 최신 워크북을 이번 주 파일로 복사하고
RAW_All(표1)에 지난주(월~일) RAW 행을 덧붙인다.

피벗 16개·슬라이서 13개가 들어 있는 워크북이라 openpyxl 로 저장하면 다 날아간다.
반드시 Excel COM(win32com)으로 열어서 쓰고 저장한다.

사용:
    python scripts/weekly_hr_raw.py --raw <앱에서 받은 HR RAW xlsx>
    python scripts/weekly_hr_raw.py --raw ... --since 2026-11-02 --until 2026-11-08   # 주 수동 지정

하는 일:
 1. 지난주 월~일 계산 (--since/--until 로 덮어쓸 수 있음)
 2. 공유드라이브 HR 폴더에서 최신 ADEF_주간_..._YYMMDD.xlsx 를 찾아 오늘 날짜 파일명으로 로컬 복사
 3. RAW 15열 + 파생 5열(연도·월·주차·week시작·week종료)을 표1 아래에 붙이고 표 범위 확장
 4. 피벗 새로고침 → 저장 → 공유드라이브로 복사
 5. runs/YYYYMMDD_weekly_hr.json 에 요약 기록

주의:
 - 날짜 열은 기존 행과 같게 **텍스트**로 쓴다. week시작/week종료는 날짜 시리얼로 쓴다
   (datetime 을 그대로 넘기면 pywin32 가 UTC 보정해서 15:00 으로 들어간다).
 - 주차는 ISO 주차. 워크북 기존 값과 일치하는 것을 확인했다(2026-10-26 → 44).
"""
import argparse
import datetime
import io
import json
import os
import re
import shutil
import sys

# 콘솔이 cp949 여도 한글 로그가 깨지지 않게
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

SHARE_DIR = r"G:\공유 드라이브\[다우오피스] 공유드라이브\리포트\주간\HR"
NAME_RE = re.compile(r"ADEF_주간_다우기술_다우오피스HR_(\d{6})\.xlsx$")
TABLE = "표1"
SHEET = "RAW_All"
NCOL = 20
EPOCH = datetime.date(1899, 12, 30)


def last_week(today=None):
    """지난주 월요일~일요일. 월요일에 돌리면 직전 월~일."""
    today = today or datetime.date.today()
    monday_this = today - datetime.timedelta(days=today.weekday())
    since = monday_this - datetime.timedelta(days=7)
    return since, since + datetime.timedelta(days=6)


def latest_workbook(share_dir):
    # 폴더명에 대괄호([다우오피스])가 있어 glob 은 쓰지 않는다 — 문자클래스로 해석된다.
    best = None
    for name in os.listdir(share_dir):
        if name.startswith("~$"):
            continue
        m = NAME_RE.search(name)
        if not m:
            continue
        if best is None or m.group(1) > best[0]:
            best = (m.group(1), os.path.join(share_dir, name))
    if best is None:
        raise SystemExit(f"공유드라이브에서 기준 워크북을 못 찾음: {share_dir}")
    return best[1], best[0]


def read_raw(path, since, until):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["RAW"]
    it = ws.iter_rows(values_only=True)
    header = next(it)
    if str(header[0]).strip() != "브랜드":
        raise SystemExit(f"RAW 헤더가 예상과 다름: {header[:3]}")
    rows, brands, out_of_range = [], set(), 0
    for r in it:
        if r[0] is None:
            continue
        ymd = str(r[4])[:10]
        if not (since <= ymd <= until):
            out_of_range += 1
            continue
        brands.add(str(r[0]))
        rows.append([r[i] for i in range(15)])
    if out_of_range:
        raise SystemExit(f"RAW 에 기간({since}~{until}) 밖 행이 {out_of_range}개 — 기간을 잘못 받은 듯")
    if brands - {"HR"}:
        raise SystemExit(f"HR 외 브랜드가 섞여 있음: {sorted(brands)} — 'HR만' 파일을 받을 것")
    if not rows:
        raise SystemExit("RAW 에 해당 기간 행이 없음")
    return rows


def append_rows(wb_path, rows, since_d, until_d):
    """Excel COM 으로 표1 에 덧붙이고 저장. (피벗·슬라이서 보존)"""
    import win32com.client as win32

    week_no = since_d.isocalendar()[1]
    ser_s = (since_d - EPOCH).days
    ser_e = (until_d - EPOCH).days
    body = [r + [since_d.year, since_d.month, week_no, None, None] for r in rows]

    xl = wb = None
    info = {}
    try:
        xl = win32.DispatchEx("Excel.Application")
        xl.Visible = False
        xl.DisplayAlerts = False
        xl.AskToUpdateLinks = False
        wb = xl.Workbooks.Open(wb_path, UpdateLinks=0)
        sh = wb.Worksheets(SHEET)
        lo = sh.ListObjects(TABLE)
        before = lo.Range.Rows.Count - 1
        if lo.Range.Columns.Count != NCOL:
            raise SystemExit(f"{TABLE} 열 수가 {lo.Range.Columns.Count} (20 기대) — 양식이 바뀜")

        # 같은 주차가 이미 들어 있으면 중복 적재 방지
        last_week_no = sh.Cells(lo.Range.Row + lo.Range.Rows.Count - 1, 18).Value
        if last_week_no is not None and int(last_week_no) == week_no:
            raise SystemExit(f"{week_no}주차가 이미 적재돼 있음 — 중복 방지로 중단")

        first = lo.Range.Row + lo.Range.Rows.Count
        last = first + len(body) - 1
        fmt = sh.Cells(first - 1, 19).NumberFormat          # 기존 week시작 서식

        sh.Range(sh.Cells(first, 5), sh.Cells(last, 5)).NumberFormat = "@"   # 날짜 = 텍스트
        sh.Range(sh.Cells(first, 1), sh.Cells(last, NCOL)).Value = tuple(tuple(r) for r in body)
        for col in (19, 20):
            rng = sh.Range(sh.Cells(first, col), sh.Cells(last, col))
            rng.NumberFormat = fmt
            rng.Value = ser_s if col == 19 else ser_e

        lo.Resize(sh.Range(sh.Cells(lo.Range.Row, 1), sh.Cells(last, NCOL)))
        after = lo.Range.Rows.Count - 1

        wb.RefreshAll()
        xl.CalculateUntilAsyncQueriesDone()
        wb.Save()
        info = {"rows_before": before, "rows_after": after, "added": after - before,
                "week_no": week_no, "table_range": lo.Range.Address}
    finally:
        try:
            if wb is not None:
                wb.Close(False)
        except Exception as e:
            print("close 경고:", e)
        try:
            if xl is not None:
                xl.Quit()
        except Exception as e:
            print("quit 경고:", e)
    return info


def verify(path, week_no, expect_added):
    """저장본을 다시 읽어 피벗·슬라이서가 남아 있고 새 주차가 들어갔는지 확인."""
    import zipfile
    from openpyxl import load_workbook
    z = zipfile.ZipFile(path)
    names = z.namelist()
    pivots = sum(1 for n in names if "pivotTable" in n and n.endswith(".xml"))
    slicers = sum(1 for n in names if "slicer" in n.lower())
    cache = z.read("xl/pivotCache/pivotCacheDefinition1.xml").decode("utf-8", "ignore")
    rec = re.findall(r'recordCount="(\d+)"', cache)
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]
    it = ws.iter_rows(values_only=True)
    next(it)
    got = sum(1 for r in it if r[0] is not None and r[17] == week_no)
    problems = []
    if pivots == 0:
        problems.append("피벗테이블이 사라짐")
    if slicers == 0:
        problems.append("슬라이서가 사라짐")
    if got != expect_added:
        problems.append(f"{week_no}주차 행이 {got}개 (기대 {expect_added})")
    return {"pivot_tables": pivots, "slicers": slicers,
            "pivot_record_count": int(rec[0]) if rec else None,
            "week_rows": got, "problems": problems}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="앱에서 받은 'HR만' RAW xlsx 경로")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--share-dir", default=SHARE_DIR)
    ap.add_argument("--work-dir", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "runs"))
    ap.add_argument("--no-upload", action="store_true", help="공유드라이브로 복사하지 않음")
    args = ap.parse_args()

    if bool(args.since) != bool(args.until):
        raise SystemExit("--since 와 --until 은 같이 지정할 것")
    if args.since:
        since_d = datetime.date.fromisoformat(args.since)
        until_d = datetime.date.fromisoformat(args.until)
    else:
        since_d, until_d = last_week()
    since, until = since_d.isoformat(), until_d.isoformat()
    print(f"대상 주: {since} ~ {until} (ISO {since_d.isocalendar()[1]}주차)")

    if not os.path.isdir(args.share_dir):
        raise SystemExit(f"공유드라이브 경로 없음: {args.share_dir}\n"
                         "→ 구글 드라이브(GoogleDriveFS)가 떠 있는지 확인할 것")

    base, base_tag = latest_workbook(args.share_dir)
    print(f"기준 워크북: {os.path.basename(base)}")

    out_tag = datetime.date.today().strftime("%y%m%d")
    out_name = f"ADEF_주간_다우기술_다우오피스HR_{out_tag}.xlsx"
    if out_tag == base_tag:
        raise SystemExit(f"오늘 날짜 파일({out_name})이 이미 공유드라이브에 있음 — 중복 방지로 중단")

    os.makedirs(args.work_dir, exist_ok=True)
    local = os.path.join(args.work_dir, out_name)
    shutil.copy2(base, local)
    print(f"로컬 복사본: {local}")

    rows = read_raw(args.raw, since, until)
    print(f"RAW {len(rows)}행 (HR, {since}~{until})")

    info = append_rows(local, rows, since_d, until_d)
    print(f"표1 {info['rows_before']} → {info['rows_after']} (+{info['added']})")

    chk = verify(local, info["week_no"], len(rows))
    print(f"검증: 피벗 {chk['pivot_tables']}개 / 슬라이서 {chk['slicers']}개 / "
          f"{info['week_no']}주차 {chk['week_rows']}행 / 피벗레코드 {chk['pivot_record_count']}")
    if chk["problems"]:
        raise SystemExit("검증 실패: " + "; ".join(chk["problems"]) + f"\n(로컬 파일 유지: {local})")

    uploaded = None
    if not args.no_upload:
        uploaded = os.path.join(args.share_dir, out_name)
        shutil.copy2(local, uploaded)
        print(f"공유드라이브 업로드: {uploaded}")

    summary = {
        "status": "ok",
        "target_week": {"from": since, "to": until, "week_no": info["week_no"]},
        "base_file": os.path.basename(base),
        "output_file": out_name,
        "local_path": local,
        "uploaded_path": uploaded,
        "raw_rows": len(rows),
        "table": info,
        "verify": chk,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    sp = os.path.join(args.work_dir, f"{datetime.date.today():%Y%m%d}_weekly_hr.json")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"요약: {sp}")


if __name__ == "__main__":
    main()
