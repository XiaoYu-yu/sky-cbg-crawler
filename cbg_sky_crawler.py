# -*- coding: utf-8 -*-
"""
藏宝阁光遇账号身高爬虫
阶段1 crawl-list   : 翻页拉取在售商品列表 -> cbg_data/list.json
阶段2 crawl-height : 逐个拉取身高JSON(静态CDN,带磁盘缓存) -> cbg_data/heights.json
阶段3 filter       : 按身高/永矮/价格/毕业季数等条件筛选,输出链接清单

仅用 Python 标准库,无需安装任何依赖。
用法示例:
  python cbg_sky_crawler.py crawl
  python cbg_sky_crawler.py filter --min-cur 1.0 --max-cur 2.0 --price-max 2000
  python cbg_sky_crawler.py filter --perm-short
  python cbg_sky_crawler.py filter --min-season 20 --price-max 3000 --top 30
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cbg_data")
DESC_DIR = os.path.join(BASE_DIR, "desc")
LIST_FILE = os.path.join(BASE_DIR, "list.json")
HEIGHTS_FILE = os.path.join(BASE_DIR, "heights.json")

LIST_API = "https://sky.cbg.163.com/cgi/api/keyword_query"
DESC_API = "https://cbg-other-desc.res.netease.com/sky/static/equipdesc/{}.json"
PRODUCT_URL = "https://sky.cbg.163.com/cgi/mweb/equip/1/{}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def http_get(url, referer=None, timeout=20, retries=3):
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Referer": referer or "https://sky.cbg.163.com/",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # 明确不存在,不重试
            last_err = e
        except Exception as e:
            last_err = e
        time.sleep(1.5 * (i + 1) + random.random())
    print("  [WARN] 请求失败 %s: %s" % (url, last_err), flush=True)
    return None


def crawl_list(page_delay=0.55, max_pages=1200, extra_qs="", merge=False):
    os.makedirs(BASE_DIR, exist_ok=True)
    items, seen = {}, set()
    if merge and os.path.exists(LIST_FILE):
        try:
            for it in json.load(open(LIST_FILE, encoding="utf-8"))["items"]:
                items[it["sn"]] = it
                seen.add(it["sn"])
        except Exception:
            pass
    page, empty_streak, latest_total = 1, 0, 0
    part_total = None
    while page <= max_pages:
        url = "%s?page=%d%s" % (LIST_API, page, extra_qs)
        body = http_get(url)
        if body is None:
            empty_streak += 1
            if empty_streak >= 3:
                print("[list] 连续失败,提前结束", flush=True)
                break
            page += 1
            continue
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            empty_streak += 1
            page += 1
            continue
        if data.get("status_code") == "CAPTCHA_AUTH" or data.get("status") == 3:
            print("[list] 触发安全验证(CAPTCHA),停止列表爬取", flush=True)
            break
        result = data.get("result") or []
        latest_total = data.get("total_num") or latest_total
        if merge and part_total is None:
            part_total = latest_total
        new_cnt = 0
        for it in result:
            sn = it.get("game_ordersn")
            if not sn or sn in seen:
                continue
            seen.add(sn)
            items[sn] = {
                "sn": sn,
                "price": it.get("price", 0),           # 单位:分
                "season": _season_num(it.get("equip_name", "")),
                "season_txt": it.get("equip_name", ""),
                "collect_num": it.get("collect_num", 0),
                "selling_time": it.get("selling_time", ""),
                "expire_s": it.get("expire_remain_seconds", 0),
                "status": it.get("status"),
                "allow_bargain": it.get("allow_bargain", False),
                "is_random_draw": it.get("is_random_draw_period", False),
            }
            new_cnt += 1
        if page == 1 or page % 20 == 0:
            print("[list] page=%d 累计=%d total=%s" % (page, len(items), latest_total), flush=True)
            _save_list(items, latest_total)
        # 结束判定
        if not result:
            break
        if merge:
            # 分区模式: 按分区总数翻满页数(每块<=1400<100页上限),不用重复检测
            if part_total and page >= math.ceil(part_total / 15):
                break
        else:
            if new_cnt == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    print("[list] 翻到尽头(整页重复),结束", flush=True)
                    break
            else:
                empty_streak = 0
            if latest_total and page >= math.ceil(latest_total / 15) + 5:
                print("[list] 超过总页数,结束", flush=True)
                break
        page += 1
        time.sleep(page_delay + random.random() * 0.3)
    _save_list(items, latest_total)
    print("[list] 完成: %d 个商品 -> %s" % (len(items), LIST_FILE), flush=True)
    return list(items.values())


def _save_list(items, total):
    with open(LIST_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "total_num": total, "items": list(items.values())},
                  f, ensure_ascii=False, indent=1)


def _season_num(name):
    m = re.search(r"(\d+)", name or "")
    return int(m.group(1)) if m else 0


# ---------- 身高计算 ----------
# 当前 = 7.6 - 8.3*S - 3*H ; 最高 = 1.6 - 8.3*S ; 最矮 = 13.6 - 8.3*S
# 永矮(H=-3) = 最高 + 15 = 16.6 - 8.3*S
def compute_heights(s, h):
    cur = 7.6 - 8.3 * s - 3 * h
    top = 1.6 - 8.3 * s
    low = 13.6 - 8.3 * s
    return cur, top, low


def fetch_one(sn):
    path = os.path.join(DESC_DIR, sn + ".json")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return sn, f.read()
    body = http_get(DESC_API.format(sn))
    time.sleep(0.12 + random.random() * 0.18)
    if body is None:
        return sn, None
    with open(path, "wb") as f:
        f.write(body)
    return sn, body


def _parse_desc(sn, body):
    try:
        d = json.loads(body.decode("utf-8"))
        rc = json.loads(d["equip_desc"])["raw_content"]
        outfit = rc.get("equipped_outfit") or {}
        h, s = outfit.get("height"), outfit.get("scale")
        if h is None or s is None:
            return None
        cur, top, low = compute_heights(s, h)
        return {
            "h": h, "s": s,
            "cur": round(cur, 4), "top": round(top, 4), "low": round(low, 4),
            "perm_short": (h == -3),
            "season": rc.get("season_graduation_count", 0),
            "candles": rc.get("candles", 0),
            "heart": rc.get("heart", 0),
            "prestige": rc.get("prestige", 0),
            "wingless": rc.get("is_wingless", False),
        }
    except Exception:
        return None


def crawl_height(list_items, workers=6):
    os.makedirs(DESC_DIR, exist_ok=True)
    heights, errors = {}, []
    todo = [it["sn"] for it in list_items]
    print("[desc] 待抓取 %d 个" % len(todo), flush=True)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for sn, body in ex.map(fetch_one, todo):
            done += 1
            if done % 200 == 0:
                print("[desc] 进度 %d/%d" % (done, len(todo)), flush=True)
            if body is None:
                errors.append({"sn": sn, "err": "404/网络失败"})
                continue
            parsed = _parse_desc(sn, body)
            if parsed is None:
                errors.append({"sn": sn, "err": "无height/scale或解析失败"})
                continue
            heights[sn] = parsed
            if done % 200 == 0:
                _save_heights(heights)
    _save_heights(heights)
    print("[desc] 完成: 成功 %d, 失败 %d -> %s" % (len(heights), len(errors), HEIGHTS_FILE), flush=True)
    if errors:
        with open(os.path.join(BASE_DIR, "errors.json"), "w", encoding="utf-8") as f:
            json.dump(errors, f, ensure_ascii=False, indent=1)
    return heights


def _save_heights(heights):
    with open(HEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "count": len(heights), "heights": heights}, f, ensure_ascii=False, indent=1)


def rebuild_heights():
    """从磁盘 desc 缓存重建 heights.json(可在爬取中途运行,查看部分结果)"""
    heights = {}
    if os.path.isdir(DESC_DIR):
        for fn in os.listdir(DESC_DIR):
            if not fn.endswith(".json"):
                continue
            sn = fn[:-5]
            try:
                with open(os.path.join(DESC_DIR, fn), "rb") as f:
                    parsed = _parse_desc(sn, f.read())
                if parsed:
                    heights[sn] = parsed
            except Exception:
                pass
    _save_heights(heights)
    print("[rebuild] %d 条身高数据 -> %s" % (len(heights), HEIGHTS_FILE), flush=True)
    return heights


def _probe_total(extra_qs):
    body = http_get("%s?page=1%s" % (LIST_API, extra_qs))
    if body is None:
        return None
    try:
        d = json.loads(body.decode("utf-8"))
        if d.get("status_code") == "CAPTCHA_AUTH" or d.get("status") == 3:
            print("[part] 触发安全验证(CAPTCHA),暂停分区爬取", flush=True)
            raise RuntimeError("CAPTCHA_AUTH")
        return d.get("total_num", 0)
    except RuntimeError:
        raise
    except Exception:
        return None


def crawl_partitioned(lo=0, hi=10000000, threshold=1400, depth=0, page_delay=0.5):
    """自适应价格分区: 每块<=threshold个,绕过匿名100页上限,爬全量"""
    qs = "&price_min=%d&price_max=%d" % (lo, hi)
    total = _probe_total(qs)
    time.sleep(page_delay)
    if total is None:
        print("[part] 探测失败 [%d,%d],跳过" % (lo, hi), flush=True)
        return
    if total == 0:
        return
    if total <= threshold or lo >= hi:
        print("[part] 区间[%d,%d]分=%d元-%d元 total=%d 开始翻页" % (
            lo, hi, lo / 100, hi / 100, total), flush=True)
        crawl_list(extra_qs=qs, merge=True, max_pages=100, page_delay=page_delay)
        return
    mid = (lo + hi) // 2
    crawl_partitioned(lo, mid, threshold, depth + 1, page_delay)
    crawl_partitioned(mid + 1, hi, threshold, depth + 1, page_delay)


def wait_api_clear(max_wait=3600, interval=90):
    """轮询等待风控解除"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        body = http_get("%s?page=1" % LIST_API)
        ok = False
        if body:
            try:
                d = json.loads(body.decode("utf-8"))
                ok = d.get("status_code") != "CAPTCHA_AUTH" and d.get("status") != 3 and "result" in d
            except Exception:
                pass
        if ok:
            print("[wait] 风控已解除,开始爬取", flush=True)
            return True
        print("[wait] 仍在风控中,%.0fs后重试 (已等%.0fs)" % (interval, time.time() - t0), flush=True)
        time.sleep(interval)
    print("[wait] 等待超时,风控未解除", flush=True)
    return False


def run_slowfull():
    if not wait_api_clear():
        return
    try:
        crawl_partitioned(page_delay=2.5)
    except RuntimeError as e:
        print("[slowfull] 列表中断: %s" % e, flush=True)
    list_items, _ = load_caches()
    if list_items:
        crawl_height(list_items)


def run_full():
    try:
        crawl_partitioned()
    except RuntimeError as e:
        print("[full] 列表阶段中断: %s (已爬部分仍会抓身高)" % e, flush=True)
    list_items, heights = load_caches()
    if list_items:
        crawl_height(list_items)
    return list_items


def load_caches():
    list_items, heights = [], {}
    if os.path.exists(LIST_FILE):
        list_items = json.load(open(LIST_FILE, encoding="utf-8"))["items"]
    if os.path.exists(HEIGHTS_FILE):
        heights = json.load(open(HEIGHTS_FILE, encoding="utf-8"))["heights"]
    return list_items, heights


def run_crawl(skip_list=False, skip_desc=False):
    list_items, heights = load_caches()
    if not skip_list:
        list_items = crawl_list()
    if not skip_desc and list_items:
        need = [it["sn"] for it in list_items if it["sn"] not in heights]
        if need:
            crawl_height(list_items)
        else:
            print("[desc] 全部已有缓存,跳过", flush=True)
    return list_items


def run_filter(a):
    list_items, heights = load_caches()
    if not list_items or not heights:
        print("缺少数据,先运行: python cbg_sky_crawler.py crawl")
        return
    rows = []
    for it in list_items:
        h = heights.get(it["sn"])
        if not h:
            continue
        if a.perm_short and not h["perm_short"]:
            continue
        if a.min_cur is not None and h["cur"] < a.min_cur:
            continue
        if a.max_cur is not None and h["cur"] > a.max_cur:
            continue
        if a.min_top is not None and h["top"] < a.min_top:
            continue
        if a.max_top is not None and h["top"] > a.max_top:
            continue
        price_yuan = it["price"] / 100.0
        if a.price_min is not None and price_yuan < a.price_min:
            continue
        if a.price_max is not None and price_yuan > a.price_max:
            continue
        if a.min_season and (h["season"] or it["season"] or 0) < a.min_season:
            continue
        if a.wing == "wing" and h["wingless"]:
            continue
        if a.wing == "wingless" and not h["wingless"]:
            continue
        if a.min_candles and h["candles"] < a.min_candles:
            continue
        rows.append({
            "cur": h["cur"], "perm": h["perm_short"], "h": h["h"], "s": h["s"],
            "top": h["top"], "low": h["low"],
            "price": price_yuan, "season": h["season"] or it["season"],
            "candles": h["candles"], "heart": h["heart"],
            "collect": it["collect_num"], "selling": it["selling_time"],
            "expire_h": round(it["expire_s"] / 3600.0, 1),
            "draw": it["is_random_draw"], "url": PRODUCT_URL.format(it["sn"]),
        })
    rows.sort(key=lambda r: r["price"])
    rows = rows[:a.top]
    print("符合条件: %d 条%s" % (len(rows), (", 最低 %d 条:" % a.top) if len(rows) > a.top else ""))
    fmt = "%-9s %-4s %-10s %-9s %-6s %-8s %-8s %-6s %-6s %-6s %s"
    print(fmt % ("当前身高", "永矮", "价格(元)", "毕业季数", "白蜡", "爱心", "收藏", "剩余h", "摇号", "上架时间", "链接"))
    for r in rows:
        print(fmt % (
            ("%.4f" % r["cur"]), ("是" if r["perm"] else "-"),
            ("%.0f" % r["price"]), str(r["season"]), str(r["candles"]),
            str(r["heart"]), str(r["collect"]), str(r["expire_h"]),
            ("是" if r["draw"] else "-"), r["selling"][:16], r["url"]))
    if a.out:
        import csv
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["当前身高", "永矮", "价格(元)", "毕业季数", "白蜡", "爱心", "收藏数",
                        "剩余小时", "摇号期", "上架时间", "链接"])
            for r in rows:
                w.writerow(["%.4f" % r["cur"], "是" if r["perm"] else "", "%.0f" % r["price"],
                            r["season"], r["candles"], r["heart"], r["collect"],
                            r["expire_h"], "是" if r["draw"] else "", r["selling"], r["url"]])
        print("CSV 已保存 -> %s" % a.out)


def main():
    p = argparse.ArgumentParser(description="藏宝阁光遇身高爬虫")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("crawl", help="抓取列表+身高(带缓存,可断点续跑)")
    sp.add_argument("--skip-list", action="store_true", help="跳过列表,用已有缓存")
    sp.add_argument("--skip-desc", action="store_true", help="跳过身高抓取")

    sub.add_parser("rebuild", help="从desc缓存重建heights.json")
    sub.add_parser("full", help="价格分区爬全量列表+身高(绕过100页上限)")
    sub.add_parser("slowfull", help="等风控解除后低速爬全量(后台续爬用)")

    sf = sub.add_parser("filter", help="按条件筛选")
    sf.add_argument("--min-cur", type=float, default=None, help="当前身高下限(号数)")
    sf.add_argument("--max-cur", type=float, default=None, help="当前身高上限(号数)")
    sf.add_argument("--min-top", type=float, default=None, help="最高身高下限")
    sf.add_argument("--max-top", type=float, default=None, help="最高身高上限")
    sf.add_argument("--perm-short", action="store_true", help="只要永矮(H=-3)")
    sf.add_argument("--price-min", type=float, default=None, help="价格下限(元)")
    sf.add_argument("--price-max", type=float, default=None, help="价格上限(元)")
    sf.add_argument("--min-season", type=int, default=0, help="毕业季数下限")
    sf.add_argument("--min-candles", type=int, default=0, help="白蜡下限")
    sf.add_argument("--wing", choices=["all", "wing", "wingless"], default="all")
    sf.add_argument("--top", type=int, default=50, help="最多输出条数(按价格升序)")
    sf.add_argument("--out", default=None, help="CSV输出路径")

    a = p.parse_args()
    if a.cmd == "crawl":
        run_crawl(a.skip_list, a.skip_desc)
    elif a.cmd == "full":
        run_full()
    elif a.cmd == "slowfull":
        run_slowfull()
    elif a.cmd == "rebuild":
        rebuild_heights()
    elif a.cmd == "filter":
        run_filter(a)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
