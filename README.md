# sky-cbg-crawler · 光遇藏宝阁身高爬虫实战

纯 Python 3 标准库，零依赖。自动爬取网易藏宝阁光遇全部在售账号，读取每个号的体型/身高数据，按条件（如「最高身高 -0.47」巨人号、永矮、价格、毕业季数）筛出商品链接。

## 快速开始

```bash
python cbg_sky_crawler.py slowfull     # 等风控+低速爬全量(列表+身高)
python cbg_sky_crawler.py filter --max-top -0.44 --top 200 --out result.csv
```

数据落在 `cbg_data/`（已 gitignore，不入库）：`list.json` 列表、`desc/*.json` 身高缓存、`heights.json` 身高表。带缓存可断点续跑。

## 三个关键地址

| 用途 | 地址 |
|---|---|
| 列表 | `GET https://sky.cbg.163.com/cgi/api/keyword_query?page=N[&price_min=X&price_max=Y]` |
| 身高 | `GET https://cbg-other-desc.res.netease.com/sky/static/equipdesc/{game_ordersn}.json` |
| 商品页 | `https://sky.cbg.163.com/cgi/mweb/equip/1/{game_ordersn}` |

- 列表必须带浏览器 `User-Agent` 和 `Referer: https://sky.cbg.163.com/`，否则 403。
- `game_ordersn` 是贯穿三者的同一 ID。
- 身高 JSON 两层解析：`equip_desc`(字符串) → `json.loads` → `raw_content.equipped_outfit` 的 `scale`(S)/`height`(H)。
- `price_min/price_max` 单位为分。

## 身高公式

```
当前 = 7.6 - 8.3*S - 3*H
最高 = 1.6 - 8.3*S
最矮 = 13.6 - 8.3*S
永矮 = (H == -3)，永矮身高 = 最高 + 15
```

数值越小越高，理论极限约 -0.475（S=0.25，巨人号）。「最高 -0.47」档即 `最高 <= -0.44`。

## 两个坑与对策

1. 匿名翻页上限 100 页（1500 个）。对策：价格二分分区，使每块 `total_num<=1400` 再翻满，按 ID 去重合并得全量。
2. 请求过密触发风控，返回 `status_code=CAPTCHA_AUTH`。对策：检测即停、间隔放慢到 ~2.5s/页、等冷却续爬。身高 CDN 不限流，可 6 线程并发。

## 筛选参数

```
--min-cur/--max-cur  当前身高范围
--min-top/--max-top  最高身高范围
--perm-short         只要永矮
--price-min/--price-max  价格(元)
--min-season         毕业季数下限
--min-candles        白蜡下限
--wing all|wing|wingless
--top N  --out x.csv
```

## 说明

仅用于个人学习/选号参考，抓取的是平台公开展示数据；请控制频率、遵守平台规则。
