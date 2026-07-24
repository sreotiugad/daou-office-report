"""
결합 파이프라인 — GAS "RAW 생성기 v5"의 7~10절을 포팅.

입력:
  ad_data  : [{"source": AD_SOURCE cfg, "records": [ad record...]}, ...]
  ga_data  : [{"source": GA_SOURCE cfg, "records": [ga record...]}, ...]
  media_index : {캠페인: {brand, gubun}}
  ga_index    : {byKey, byMedium, whitelist, count}
  meta_map    : {data:{key:content}, count}

출력: {"rows": [...15컬럼...], "stats": {...}}
"""
from .config import (
    AD_SOURCES, GA_SOURCES, UNCLASSIFIED, GA_SKIP_CONTENT,
    LEFTOVER_TAG, INCLUDE_NOSET_IN_LEFTOVER, DUP_MODE, ROUND_COST,
)
from .helpers import (
    to_num, to_str, normalize_date, normalize_device, norm_medium,
    make_join_key, make_meta_key, make_ga_index_key, apply_brand_override,
)


def new_stats():
    return {
        "ad": [], "ga": [],
        "outIndexMediums": {}, "unclassifiedCampaigns": {}, "metaMissList": {},
        "gaByGroup": {},
        "leftoverRows": 0, "leftoverConv": 0, "leftoverEmp": 0,
        "dupKeys": 0, "dupRows": 0, "brandOverride": 0,
    }


# ── 기간 필터 헬퍼 ──────────────────────────────────────────
def _in_range(ymd, since, until):
    """since/until 이 지정됐으면 [since, until] 안의 날짜만 True.
    날짜가 없거나(합계행 등) 범위 밖이면 False. 기간 미지정이면 항상 True."""
    if not since or not until:
        return True
    if not ymd:
        return False
    return since <= ymd <= until


# ── 7. GA 집계 (화이트리스트 + 6중 키) ─────────────────────
def build_ga_maps(ga_data, ga_index, stats, since=None, until=None):
    conv_map, emp_map, detail = {}, {}, {}

    for entry in ga_data:
        src = entry["source"]
        records = entry.get("records") or []
        st = {"sheet": src["sheet"], "brand": src["brand"], "rawRows": 0,
              "totalConv": 0, "totalEmp": 0, "inIndexConv": 0, "inIndexEmp": 0,
              "outIndexConv": 0, "outIndexRows": 0, "notsetConv": 0, "notsetEmp": 0,
              "found": entry.get("found", True)}
        stats["ga"].append(st)

        for row in records:
            medium_raw = to_str(row.get("medium"))
            ymd = normalize_date(row.get("date"))
            conv = to_num(row.get("conv"))
            emp = to_num(row.get("emp"))

            if not medium_raw and not ymd and conv == 0 and emp == 0:
                continue
            # 선택 기간 밖(또는 날짜 없는 합계행)은 제외
            if not _in_range(ymd, since, until):
                continue
            st["rawRows"] += 1
            st["totalConv"] += conv
            st["totalEmp"] += emp

            nm = norm_medium(medium_raw)
            if not ga_index["whitelist"].get(nm):
                st["outIndexConv"] += conv
                st["outIndexRows"] += 1
                k = medium_raw or "(빈칸)"
                stats["outIndexMediums"][k] = stats["outIndexMediums"].get(k, 0) + conv
                continue
            if not ymd:
                continue
            if conv == 0 and emp == 0:
                continue

            st["inIndexConv"] += conv
            st["inIndexEmp"] += emp

            gi = ga_index["byKey"].get(make_ga_index_key(src["brand"], medium_raw)) \
                or ga_index["byMedium"].get(nm)
            gubun = gi["gubun"] if gi else UNCLASSIFIED
            media = gi["media"] if gi else UNCLASSIFIED
            device = gi["device"] if gi else ""

            content = to_str(row.get("content"))
            matchable = content not in GA_SKIP_CONTENT
            if not matchable:
                st["notsetConv"] += conv
                st["notsetEmp"] += emp

            gkey = src["brand"] + "\t" + gubun + "\t" + media
            g = stats["gaByGroup"].setdefault(gkey, {"conv": 0, "noset": 0, "emp": 0, "empNoset": 0})
            g["conv"] += conv
            g["emp"] += emp
            if not matchable:
                g["noset"] += conv
                g["empNoset"] += emp

            jk = make_join_key(src["brand"], gubun, media, device, ymd, content)

            if matchable:
                conv_map[jk] = conv_map.get(jk, 0) + conv
                emp_map[jk] = emp_map.get(jk, 0) + emp

            d = detail.get(jk)
            if d is None:
                d = detail[jk] = {
                    "brand": src["brand"], "gubun": gubun, "media": media, "device": device,
                    "ymd": ymd, "content": content, "medium": medium_raw,
                    "campaign": to_str(row.get("campaign")),
                    "joinKey": jk, "matchable": matchable, "conv": 0, "emp": 0,
                }
            d["conv"] += conv
            d["emp"] += emp

    return {"convMap": conv_map, "empMap": emp_map, "detail": detail}


# ── 8. 광고 소스 → RAW 행 (1단계) ──────────────────────────
def build_ad_rows(ad_data, media_index, meta_map, stats, since=None, until=None):
    rows = []
    key_groups = {}

    for entry in ad_data:
        src = entry["source"]
        records = entry.get("records") or []
        st = {"label": src["label"], "rawRows": 0, "outRows": 0,
              "skipEmpty": 0, "skipFilter": 0, "imp": 0, "click": 0, "cost": 0,
              "metaMiss": 0, "unclassified": 0, "found": entry.get("found", True),
              "multiplier": src.get("costMultiplier", 1)}
        stats["ad"].append(st)

        use_meta = src.get("useMetaMap", False)
        filter_kw = src.get("filterKeyword")
        fixed_device = src.get("device")
        multiplier = src.get("costMultiplier")

        for rec in records:
            st["rawRows"] += 1
            campaign = to_str(rec.get("campaign"))
            ymd = normalize_date(rec.get("date"))
            if not campaign and not ymd:
                st["skipEmpty"] += 1
                continue
            # 선택 기간 밖은 제외 (업로드 파일이 더 긴 기간이어도 그 주만 집계)
            if since and until and not _in_range(ymd, since, until):
                st["skipRange"] = st.get("skipRange", 0) + 1
                continue
            if filter_kw and filter_kw not in campaign:
                st["skipFilter"] += 1
                continue

            ad_group = to_str(rec.get("adGroup"))
            ad_name = to_str(rec.get("adName"))

            idx = media_index.get(campaign)
            brand = idx["brand"] if idx else UNCLASSIFIED
            gubun = idx["gubun"] if idx else UNCLASSIFIED
            if not idx:
                st["unclassified"] += 1
                uk = src["label"] + "\t" + campaign
                stats["unclassifiedCampaigns"][uk] = stats["unclassifiedCampaigns"].get(uk, 0) + 1

            brand_fixed = apply_brand_override(brand, gubun, ad_group)
            if brand_fixed != brand:
                stats["brandOverride"] += 1
                brand = brand_fixed

            device_raw = fixed_device if isinstance(fixed_device, str) else rec.get("device_raw")
            device = normalize_device(device_raw)

            # GA 결합용 콘텐츠 결정
            join_content = ad_name
            if use_meta:
                mk = make_meta_key(campaign, ad_group, ad_name)
                if mk in meta_map["data"]:
                    join_content = meta_map["data"][mk]
                else:
                    join_content = ""
                    st["metaMiss"] += 1
                    stats["metaMissList"][campaign + "\t" + ad_group + "\t" + ad_name] = True

            imp = to_num(rec.get("imp"))
            click = to_num(rec.get("click"))
            cost = to_num(rec.get("cost"))
            if multiplier:
                cost = cost * multiplier
                if ROUND_COST:
                    cost = round(cost)

            st["imp"] += imp
            st["click"] += click
            st["cost"] += cost
            st["outRows"] += 1

            row = [
                brand, gubun, src["label"], device,
                ymd or "",
                campaign, ad_group, ad_name,
                imp, click, cost,
                to_num(rec.get("rank")),
                to_num(rec.get("view")),
                0, 0,  # 전환·직원수는 2단계에서 채움
            ]
            row_idx = len(rows)
            rows.append(row)

            if join_content and ymd:
                jk = make_join_key(brand, gubun, src["label"], device, ymd, join_content)
                key_groups.setdefault(jk, []).append({"i": row_idx, "click": click})

    return {"rows": rows, "keyGroups": key_groups}


# ── 9. GA 전환 배분 (2단계) ────────────────────────────────
def assign_ga_to_ad_rows(rows, key_groups, ga_maps, used_keys, stats):
    conv_map = ga_maps["convMap"]
    emp_map = ga_maps["empMap"]

    for jk, group in key_groups.items():
        if jk not in conv_map:
            continue
        conv = conv_map[jk]
        emp = emp_map.get(jk, 0)
        used_keys[jk] = True

        if len(group) == 1:
            rows[group[0]["i"]][13] = conv
            rows[group[0]["i"]][14] = emp
            continue

        stats["dupKeys"] += 1
        stats["dupRows"] += len(group)

        if DUP_MODE == "all":
            for gitem in group:
                rows[gitem["i"]][13] = conv
                rows[gitem["i"]][14] = emp
        elif DUP_MODE == "split":
            total_click = sum(g["click"] for g in group)
            if total_click > 0:
                for gitem in group:
                    rows[gitem["i"]][13] = conv * gitem["click"] / total_click
                    rows[gitem["i"]][14] = emp * gitem["click"] / total_click
            else:
                for gitem in group:
                    rows[gitem["i"]][13] = conv / len(group)
                    rows[gitem["i"]][14] = emp / len(group)
        else:  # top_click
            best = group[0]
            for gitem in group:
                if gitem["click"] > best["click"]:
                    best = gitem
            rows[best["i"]][13] = conv
            rows[best["i"]][14] = emp


# ── 10. 미매칭 GA → 하단 추가 행 ───────────────────────────
def build_leftover_ga_rows(ga_maps, used_keys, stats):
    rows = []
    for jk, d in ga_maps["detail"].items():
        if d["matchable"] and used_keys.get(jk):
            continue
        if not d["matchable"] and not INCLUDE_NOSET_IN_LEFTOVER:
            continue
        rows.append([
            d["brand"], d["gubun"], d["media"], d["device"],
            d["ymd"],
            d["campaign"], LEFTOVER_TAG, d["content"],
            0, 0, 0, 0, 0,
            d["conv"], d["emp"],
        ])

    rows.sort(key=lambda r: (to_str(r[0]), to_str(r[4]), to_str(r[2])))

    stats["leftoverRows"] = len(rows)
    stats["leftoverConv"] = sum(r[13] for r in rows)
    stats["leftoverEmp"] = sum(r[14] for r in rows)
    return rows


# ── 10-2. 브랜드검색 고정비 채우기 ─────────────────────────
def _date_iter(since, until):
    from datetime import datetime, timedelta
    try:
        d = datetime.strptime(since, "%Y-%m-%d").date()
        e = datetime.strptime(until, "%Y-%m-%d").date()
    except Exception:
        return
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)


def apply_brand_search(rows, contracts, since, until, stats):
    """계약 기간 ∩ 조회기간의 매일, 지정 광고이름 행에 일일광고비를 채운다.
    해당 날짜에 그 행이 이미 있으면 광고비만 덮어쓰고, 없으면 새 행을 추가한다."""
    if not contracts or not since or not until:
        return rows
    added = 0
    filled = 0
    for c in contracts:
        lo = max(c["since"], since) if c["since"] else since
        hi = min(c["until"], until) if c["until"] else until
        if lo > hi:
            continue
        fee = c["fee"]
        for day in _date_iter(lo, hi):
            found = None
            for r in rows:
                # 광고이름만으로는 SA/BSA 동명 키워드가 섞이므로
                # 구분·캠페인·광고그룹까지(계약에 값이 있으면) 정밀 매칭한다.
                if not (to_str(r[4]) == day and to_str(r[7]) == c["adName"]
                        and to_str(r[2]) == c["media"] and to_str(r[0]) == c["brand"]
                        and to_str(r[3]) == c["device"]):
                    continue
                if c.get("gubun") and to_str(r[1]) != c["gubun"]:
                    continue
                if c.get("campaign") and to_str(r[5]) != c["campaign"]:
                    continue
                if c.get("adGroup") and to_str(r[6]) != c["adGroup"]:
                    continue
                found = r
                break
            if found is not None:
                found[10] = fee
                filled += 1
            else:
                rows.append([
                    c["brand"], c["gubun"], c["media"], c["device"], day,
                    c["campaign"], c["adGroup"], c["adName"],
                    0, 0, fee, 0, 0, 0, 0,
                ])
                added += 1
    stats["bsFilled"] = filled
    stats["bsAdded"] = added
    return rows


# ── 통합 실행 ──────────────────────────────────────────────
def run_pipeline(ad_data, ga_data, media_index, ga_index, meta_map,
                 brand_search=None, since=None, until=None):
    stats = new_stats()
    ga_maps = build_ga_maps(ga_data, ga_index, stats, since, until)
    built = build_ad_rows(ad_data, media_index, meta_map, stats, since, until)
    used_keys = {}
    assign_ga_to_ad_rows(built["rows"], built["keyGroups"], ga_maps, used_keys, stats)
    # 브랜드검색 고정비는 광고 행에 채운다(레프트오버 합류 전).
    apply_brand_search(built["rows"], brand_search or [], since, until, stats)
    leftover = build_leftover_ga_rows(ga_maps, used_keys, stats)
    all_rows = built["rows"] + leftover
    return {
        "rows": all_rows,
        "adRowCount": len(built["rows"]),
        "leftoverCount": len(leftover),
        "stats": stats,
    }
