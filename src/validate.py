"""
데이터 점검 리포트 — GAS "RAW 생성기 v5"의 12절(writeCheckSheet)을 포팅.

시트에 직접 쓰는 대신, 섹션별 DataFrame 목록을 만들어 Streamlit 표시와
xlsx 내보내기에 함께 쓴다.

build_check_report(stats, meta_map, all_rows) ->
  {"sections": [{title, df, pass}], "pass": bool, "issues": int}
"""
import pandas as pd

from .config import DUP_MODE, INCLUDE_NOSET_IN_LEFTOVER, UNCLASSIFIED
from .helpers import to_num, to_str

_EPS = 1e-4


def _status(ok, over_label="❌ 초과", under_label="❌ 누락", ok_label="✅ 일치"):
    return ok_label if ok else over_label


def build_check_report(stats, meta_map, all_rows):
    sections = []
    issues = 0

    def add(title, df, section_ok=True):
        nonlocal issues
        sections.append({"title": title, "df": df, "pass": section_ok})
        if not section_ok:
            issues += 1

    # RAW 집계 (브랜드|구분|매체)
    raw_by_group = {}
    raw_imp = raw_click = raw_cost = raw_conv = raw_emp = 0
    for r in all_rows:
        gk = to_str(r[0]) + "\t" + to_str(r[1]) + "\t" + to_str(r[2])
        g = raw_by_group.setdefault(gk, {"conv": 0, "emp": 0})
        g["conv"] += to_num(r[13])
        g["emp"] += to_num(r[14])
        raw_imp += to_num(r[8]); raw_click += to_num(r[9]); raw_cost += to_num(r[10])
        raw_conv += to_num(r[13]); raw_emp += to_num(r[14])

    # ── 1. 광고 데이터 (원본 → RAW) ──
    rows1 = []
    sec1_ok = True
    for s in stats["ad"]:
        state = "❌ 데이터 없음" if not s["found"] else ("⚠️ 미분류 있음" if s["unclassified"] > 0 else "✅ 정상")
        if not s["found"] or s["unclassified"] > 0:
            sec1_ok = False
        rows1.append([s["label"], s["rawRows"], s["outRows"], s["skipEmpty"],
                      s["skipFilter"], s["unclassified"], state])
    df1 = pd.DataFrame(rows1, columns=["매체", "원본 행수", "RAW 반영", "제외(빈행)",
                                       "제외(필터)", "미분류 캠페인", "상태"])
    add("1. 광고 데이터 (원본 → RAW)", df1, sec1_ok)

    # ── 2. 광고 지표 합계 ──
    #  광고비는 이미 ×1.1(부가세)이 적용된 값끼리 비교한다(원본 집계·RAW 모두 적용됨).
    #  다만 고정비(브랜드검색·사람인 계약)는 RAW 에만 가산되므로, 그 순증가분을
    #  '합계'에 더해 대사한다. (bsCostDelta)
    rows2 = []
    sum_imp = sum_click = sum_cost = 0
    for s in stats["ad"]:
        sum_imp += s["imp"]; sum_click += s["click"]; sum_cost += s["cost"]
        rows2.append([s["label"], s["imp"], s["click"], round(s["cost"]),
                      "-" if s["multiplier"] == 1 else f"× {s['multiplier']}"])
    rows2.append(["합계(광고원본)", sum_imp, sum_click, round(sum_cost), ""])
    bs_delta = stats.get("bsCostDelta", 0)
    expect_cost = sum_cost + bs_delta
    if bs_delta:
        rows2.append(["＋고정비(브검·사람인 계약)", "", "", round(bs_delta), "계약비 가산"])
        rows2.append(["기대 합계(광고+고정비)", sum_imp, sum_click, round(expect_cost), ""])
    imp_ok = (raw_imp == sum_imp and raw_click == sum_click and round(raw_cost) == round(expect_cost))
    rows2.append(["RAW 시트 합계", raw_imp, raw_click, round(raw_cost),
                  "✅ 일치" if imp_ok else "❌ 불일치"])
    df2 = pd.DataFrame(rows2, columns=["매체", "노출 합계", "클릭 합계", "광고비 합계", "비고/상태"])
    add("2. 광고 지표 합계 (광고원본＋고정비 = RAW 여야 정상)", df2, imp_ok)

    # 그룹 키 합치기
    group_keys = set(stats["gaByGroup"].keys()) | set(raw_by_group.keys())
    sorted_groups = sorted(group_keys)

    # ── 3-1. 브랜드·구분·매체별 전환 대사 ──
    rows31 = []
    sec31_ok = True
    tA = tB = tRaw = 0
    for k in sorted_groups:
        p = k.split("\t")
        g = stats["gaByGroup"].get(k, {"conv": 0, "noset": 0})
        b = 0 if INCLUDE_NOSET_IN_LEFTOVER else g.get("noset", 0)
        expect = g["conv"] - b
        got = raw_by_group.get(k, {"conv": 0})["conv"]
        tA += g["conv"]; tB += b; tRaw += got
        diff = got - expect
        ok = abs(diff) < _EPS
        if not ok:
            sec31_ok = False
        state = "✅ 일치" if ok else (f"❌ 초과 +{diff:g}" if diff > 0 else f"❌ 누락 {diff:g}")
        rows31.append([p[0], p[1], p[2], g["conv"], b, expect, got, state])
    total_ok = abs(tRaw - (tA - tB)) < _EPS
    rows31.append(["합계", "", "", tA, tB, tA - tB, tRaw, "✅ 일치" if total_ok else "❌ 불일치"])
    df31 = pd.DataFrame(rows31, columns=["브랜드", "구분", "매체", "GA 전환(A)",
                                         "(not set)제외(B)", "기대값(A-B)", "RAW 전환", "상태"])
    add("3-1. 브랜드·구분·매체별 [전환수] 대사", df31, sec31_ok and total_ok)

    # ── 3-2. 직원수 대사 ──
    rows32 = []
    sec32_ok = True
    eA = eB = eRaw = 0
    emp_group_count = 0
    for k in sorted_groups:
        p = k.split("\t")
        g = stats["gaByGroup"].get(k, {"emp": 0, "empNoset": 0})
        got = raw_by_group.get(k, {"emp": 0})["emp"]
        if not g.get("emp") and not got:
            continue
        emp_group_count += 1
        b = 0 if INCLUDE_NOSET_IN_LEFTOVER else g.get("empNoset", 0)
        expect = g.get("emp", 0) - b
        eA += g.get("emp", 0); eB += b; eRaw += got
        diff = got - expect
        ok = abs(diff) < _EPS
        if not ok:
            sec32_ok = False
        state = "✅ 일치" if ok else (f"❌ 초과 +{diff:g}" if diff > 0 else f"❌ 누락 {diff:g}")
        rows32.append([p[0], p[1], p[2], g.get("emp", 0), b, expect, got, state])
    if emp_group_count == 0:
        df32 = pd.DataFrame([["직원수 데이터가 없습니다.", "", "", "", "", "", "", "-"]],
                            columns=["브랜드", "구분", "매체", "GA 직원수(A)",
                                     "(not set)제외(B)", "기대값(A-B)", "RAW 직원수", "상태"])
    else:
        te_ok = abs(eRaw - (eA - eB)) < _EPS
        rows32.append(["합계", "", "", eA, eB, eA - eB, eRaw, "✅ 일치" if te_ok else "❌ 불일치"])
        sec32_ok = sec32_ok and te_ok
        df32 = pd.DataFrame(rows32, columns=["브랜드", "구분", "매체", "GA 직원수(A)",
                                             "(not set)제외(B)", "기대값(A-B)", "RAW 직원수", "상태"])
    add("3-2. 브랜드·구분·매체별 [직원수] 대사 (GA_HR 전용)", df32, sec32_ok)

    # ── 4. GA 시트별 전환 요약 ──
    rows4 = []
    sec4_ok = True
    ga_total = ga_in = ga_out = ga_noset = 0
    for s in stats["ga"]:
        ga_total += s["totalConv"]; ga_in += s["inIndexConv"]
        ga_out += s["outIndexConv"]; ga_noset += s["notsetConv"]
        state = "❌ 데이터 없음" if not s["found"] else ("⚠️ 제외분 있음" if s["outIndexConv"] > 0 else "✅ 정상")
        if not s["found"]:
            sec4_ok = False
        rows4.append([s["sheet"], s["brand"], s["totalConv"], s["inIndexConv"],
                      s["outIndexConv"], s["notsetConv"], state])
    rows4.append(["합계", "", ga_total, ga_in, ga_out, ga_noset, ""])
    rows4.append(["RAW 광고행 결합 전환", "", "", "", "", raw_conv - stats["leftoverConv"], ""])
    rows4.append(["RAW 하단 미매칭 전환", "", "", "", "", stats["leftoverConv"], ""])
    rows4.append(["RAW 전환 합계", "", "", "", "", raw_conv, ""])
    rows4.append(["RAW 직원수 합계", "", "", "", "", raw_emp, ""])
    df4 = pd.DataFrame(rows4, columns=["시트", "브랜드", "원본 전환", "INDEX 통과",
                                       "INDEX 외 제외", "(not set)제외", "상태"])
    add("4. GA 시트별 전환 요약", df4, sec4_ok)

    # ── 5. 결합 키 중복 ──
    if stats["dupKeys"] == 0:
        dup_state = "✅ 없음"
        dup_ok = True
    else:
        dup_ok = False
        dup_state = "❌ 전환 중복 계상됨" if DUP_MODE == "all" else f"⚠️ {DUP_MODE} 규칙으로 배분됨"
    df5 = pd.DataFrame([[stats["dupKeys"], stats["dupRows"], dup_state]],
                       columns=["중복 키 수", "해당 광고 행 수", "상태"])
    add("5. 결합 키 중복 (같은 키를 가진 광고 행이 2개 이상)", df5, dup_ok)

    # ── 6. GA INDEX 미등록 소스/매체 ──
    out_keys = sorted(stats["outIndexMediums"].keys(),
                      key=lambda k: -stats["outIndexMediums"][k])
    if not out_keys:
        df6 = pd.DataFrame([["없음 (모든 GA 소스/매체가 INDEX에 등록됨)"]], columns=["결과"])
        add("6. [GA INDEX]에 없어 제외된 소스/매체", df6, True)
    else:
        df6 = pd.DataFrame([[k, stats["outIndexMediums"][k]] for k in out_keys],
                           columns=["세션 소스/매체", "제외된 전환수"])
        add("6. [GA INDEX]에 없어 제외된 소스/매체", df6, False)

    # ── 7. 매체 INDEX 미등록 캠페인 ──
    uc_keys = sorted(stats["unclassifiedCampaigns"].keys())
    if not uc_keys:
        df7 = pd.DataFrame([["없음 (모든 캠페인이 INDEX에 등록됨)"]], columns=["결과"])
        add(f"7. [매체 INDEX]에 없어 {UNCLASSIFIED} 처리된 캠페인", df7, True)
    else:
        df7 = pd.DataFrame([[k.split("\t")[0], k.split("\t")[1], stats["unclassifiedCampaigns"][k]]
                            for k in uc_keys],
                           columns=["매체", "캠페인명", "행수"])
        add(f"7. [매체 INDEX]에 없어 {UNCLASSIFIED} 처리된 캠페인", df7, False)

    # ── 8. meta 정리 미매칭 ──
    mm_keys = sorted(stats["metaMissList"].keys())
    if not mm_keys:
        df8 = pd.DataFrame([[f"등록 {meta_map['count']}건", "없음 (모든 META 광고가 매핑됨)"]],
                           columns=["매핑 등록", "결과"])
        add("8. [meta 정리]에서 찾지 못한 META 광고 (전환 0 처리)", df8, True)
    else:
        shown = mm_keys[:200]
        df8 = pd.DataFrame([[k.split("\t")[0], k.split("\t")[1], k.split("\t")[2]] for k in shown],
                           columns=["캠페인", "광고세트", "광고이름"])
        add("8. [meta 정리]에서 찾지 못한 META 광고 (전환 0 처리)", df8, False)

    return {"sections": sections, "pass": issues == 0, "issues": issues}
