#!/usr/bin/env python3
"""
データ品質修正スクリプト
1. 豊島区公式ページから住所を取得
2. 住所→座標変換（Nominatim）
3. 内定指数PDFの再パース・マッチング
4. 統合データを再生成
"""

import json
import re
import time
from pathlib import Path

import pdfplumber
import requests

DATA_DIR = Path(__file__).parent / "data"

HEADERS = {"User-Agent": "hokatsu-map/1.0 (personal use)"}

# 自宅の座標
HOME_LAT = 35.7283
HOME_LNG = 139.7193

def haversine(lat1, lng1, lat2, lng2):
    import math
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def scrape_capacity_page():
    """豊島区の定員一覧HTMLから園名・住所を取得"""
    urls = [
        # 区立
        "https://www.city.toshima.lg.jp/268/20171102.html",
        # 私立
        "https://www.city.toshima.lg.jp/452/kosodate/kosodate/hoikuen/shiritsu/index.html",
        # 定員一覧
        "https://www.city.toshima.lg.jp/452/kosodate/kosodate/hoikuen/006596.html",
    ]
    # これはHTMLパースが複雑なので、代わりにオープンデータ + 手動補完で対応
    pass


def get_open_data_facilities():
    """オープンデータの施設一覧を取得"""
    filepath = DATA_DIR / "toshima_preschool.xlsx"
    if not filepath.exists():
        return {}

    import openpyxl
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active
    headers = [str(cell.value or "") for cell in ws[1]]

    facilities = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        record = {headers[i]: val for i, val in enumerate(row) if i < len(headers)}
        name = str(record.get("名称", "")).strip()
        if name:
            facilities[name] = {
                "address": str(record.get("住所", "")),
                "lat": float(record["緯度"]) if record.get("緯度") else None,
                "lng": float(record["経度"]) if record.get("経度") else None,
                "phone": str(record.get("電話番号", "")),
                "capacity": record.get("収容定員"),
                "type": str(record.get("種別", "")),
            }
    return facilities


def geocode_address(address: str) -> tuple[float, float] | None:
    """住所→座標変換（Nominatim）"""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"東京都豊島区{address}",
            "format": "json",
            "limit": 1,
            "countrycodes": "jp",
        }
        resp = requests.get(url, params=params, headers={
            "User-Agent": "hokatsu-map/1.0 (personal project)"
        }, timeout=10)
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print(f"    Geocode error for {address}: {e}")
    return None


def parse_vacancy_pdf_with_address(filepath: Path) -> list[dict]:
    """欠員状況PDFを丁寧にパース（住所も取得）"""
    results = []

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            tables = page.extract_tables()

            for table in tables:
                if not table:
                    continue

                # ヘッダー行を探す
                header_row = None
                for i, row in enumerate(table):
                    if row and any("園名" in str(c or "") for c in row):
                        header_row = i
                        break

                if header_row is not None:
                    headers = [str(c or "").strip().replace("\n", "") for c in table[header_row]]
                    print(f"  Page {page_num+1} headers: {headers}")

                for row in table:
                    if not row or len(row) < 8:
                        continue
                    no = str(row[0] or "").strip()
                    if not no or not re.match(r"^\d+$", no):
                        continue

                    name = str(row[1] or "").strip().replace("\n", "")

                    # 年齢別空き
                    ages = {}
                    age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
                    for i, label in enumerate(age_labels):
                        val = str(row[2 + i] or "").strip() if 2 + i < len(row) else ""
                        if val in ("", "-", "−", "―", "×", "☓"):
                            ages[label] = 0
                        else:
                            try:
                                ages[label] = int(re.sub(r"[^\d-]", "", val) or "0")
                            except ValueError:
                                ages[label] = 0

                    # 住所: PDFの後半カラムから取得
                    address = ""
                    for ci in range(len(row) - 1, 7, -1):
                        cell = str(row[ci] or "").strip().replace("\n", "")
                        if re.search(r"(池袋|大塚|雑司|巣鴨|目白|長崎|要町|高田|千早|駒込|高松|南長崎|千川|東池袋|南池袋|西池袋|北|上池)", cell):
                            address = cell
                            break

                    results.append({
                        "no": int(no),
                        "name": name,
                        "vacancy": ages,
                        "address_from_pdf": address,
                        "page": page_num + 1,
                    })

    return results


def parse_score_pdf_v2(filepath: Path) -> dict:
    """内定指数PDFを再パース → 園名→スコアの辞書"""
    scores = {}

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 8:
                        continue
                    no = str(row[0] or "").strip()
                    if not no or not re.match(r"^\d+$", no):
                        continue

                    name = str(row[1] or "").strip().replace("\n", "")

                    age_scores = {}
                    age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
                    for i, label in enumerate(age_labels):
                        val = str(row[2 + i] or "").strip() if 2 + i < len(row) else ""
                        if val in ("", "-", "−", "―"):
                            age_scores[label] = None
                        else:
                            try:
                                age_scores[label] = int(re.sub(r"[^\d]", "", val) or "0")
                            except ValueError:
                                age_scores[label] = None

                    scores[name] = age_scores

    return scores


def fuzzy_match_name(name: str, candidates: dict) -> str | None:
    """園名のファジーマッチ"""
    name_clean = re.sub(r"[\s　・]", "", name)

    # 完全一致
    if name in candidates:
        return name

    # クリーン後一致
    for cand in candidates:
        if re.sub(r"[\s　・]", "", cand) == name_clean:
            return cand

    # 部分一致（長い方から）
    best = None
    best_len = 0
    for cand in candidates:
        cand_clean = re.sub(r"[\s　・]", "", cand)
        # 両方向の部分一致
        if name_clean in cand_clean or cand_clean in name_clean:
            match_len = min(len(name_clean), len(cand_clean))
            if match_len > best_len and match_len >= 4:
                best = cand
                best_len = match_len

    return best


def main():
    print("=" * 60)
    print("データ品質修正")
    print("=" * 60)

    # 1. オープンデータ読み込み
    print("\n[1] オープンデータ読み込み...")
    open_data = get_open_data_facilities()
    print(f"  {len(open_data)}施設")

    # 2. 欠員状況PDF再パース
    print("\n[2] 欠員状況PDF再パース...")
    vacancy_pdfs = list(DATA_DIR.glob("vacancy_*.pdf"))
    vacancy_data = []
    for pdf_path in vacancy_pdfs:
        print(f"  Parsing: {pdf_path.name}")
        parsed = parse_vacancy_pdf_with_address(pdf_path)
        if parsed:
            vacancy_data = parsed
            break
    print(f"  {len(vacancy_data)}園パース済み")

    # 3. 内定指数PDF再パース
    print("\n[3] 内定指数PDF再パース...")
    score_pdfs = list(DATA_DIR.glob("score_*.pdf"))
    score_data = {}
    for pdf_path in score_pdfs:
        print(f"  Parsing: {pdf_path.name}")
        score_data = parse_score_pdf_v2(pdf_path)
        if score_data:
            break
    print(f"  {len(score_data)}園のスコアデータ")
    # サンプル表示
    for name, scores in list(score_data.items())[:5]:
        print(f"    {name}: {scores}")

    # 4. データ統合
    print("\n[4] データ統合...")

    integrated = []
    geocode_count = 0

    for v in vacancy_data:
        record = {
            "name": v["name"],
            "vacancy": v["vacancy"],
            "total_vacancy": sum(v["vacancy"].values()),
            "address": v.get("address_from_pdf", ""),
            "lat": None,
            "lng": None,
            "phone": "",
            "capacity": None,
            "category": "",
            "min_scores": {},
        }

        # オープンデータとマッチ
        od_match = fuzzy_match_name(v["name"], open_data)
        if od_match:
            od = open_data[od_match]
            record["lat"] = od["lat"]
            record["lng"] = od["lng"]
            record["phone"] = od.get("phone", "")
            record["capacity"] = od.get("capacity")
            record["category"] = od.get("type", "")
            if not record["address"] and od.get("address"):
                record["address"] = od["address"]

        # 内定指数とマッチ
        score_match = fuzzy_match_name(v["name"], score_data)
        if score_match:
            record["min_scores"] = score_data[score_match]

        # 座標がない or オープンデータのマッチが怪しい場合、住所からジオコード
        if not record["lat"] and record["address"] and geocode_count < 50:
            print(f"  Geocoding: {v['name']} ({record['address']})")
            coords = geocode_address(record["address"])
            if coords:
                record["lat"], record["lng"] = coords
                geocode_count += 1
                time.sleep(1.1)  # Nominatim rate limit

        # 自宅からの距離
        if record["lat"] and record["lng"]:
            record["distance_m"] = round(haversine(HOME_LAT, HOME_LNG, record["lat"], record["lng"]))
        else:
            record["distance_m"] = None

        # 入園確率（2歳）
        vacancy_2 = v["vacancy"].get("2歳", 0)
        min_score_2 = record["min_scores"].get("2歳") if record["min_scores"] else None
        record["admission_2yr"] = calc_probability(vacancy_2, min_score_2, 20)

        integrated.append(record)

    # 座標のマッチ状況
    with_coords = sum(1 for r in integrated if r["lat"])
    with_address = sum(1 for r in integrated if r["address"])
    with_scores = sum(1 for r in integrated if r["min_scores"])
    print(f"\n  座標あり: {with_coords}/{len(integrated)}")
    print(f"  住所あり: {with_address}/{len(integrated)}")
    print(f"  スコアあり: {with_scores}/{len(integrated)}")

    # 距離重複チェック
    from collections import Counter
    dists = Counter(r["distance_m"] for r in integrated if r.get("distance_m"))
    dupes = {d: c for d, c in dists.items() if c > 1}
    print(f"  距離重複: {len(dupes)}グループ")
    for d, c in sorted(dupes.items())[:5]:
        names = [r["name"] for r in integrated if r.get("distance_m") == d]
        print(f"    {d}m: {names}")

    # ソート
    integrated.sort(key=lambda x: x.get("distance_m") or 99999)

    # 保存
    from datetime import datetime
    output = {
        "generated_at": datetime.now().isoformat(),
        "home_location": {"lat": HOME_LAT, "lng": HOME_LNG},
        "my_score": 20,
        "total_facilities": len(integrated),
        "data_quality": {
            "with_coordinates": with_coords,
            "with_address": with_address,
            "with_scores": with_scores,
            "distance_duplicates": len(dupes),
        },
        "facilities": integrated,
    }

    output_path = DATA_DIR / "hokatsu_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  保存: {output_path}")

    # 1.5km圏内サマリ
    print("\n" + "=" * 60)
    print("自宅 1.5km圏内 2歳クラス")
    print("=" * 60)
    nearby = [r for r in integrated if r.get("distance_m") and r["distance_m"] <= 1500]
    for r in nearby:
        v2 = r["vacancy"].get("2歳", 0)
        s2 = r["min_scores"].get("2歳", "?") if r["min_scores"] else "?"
        s2_str = f"ボーダー{s2}" if isinstance(s2, int) else "全員内定" if s2 is None else "?"
        prob = r["admission_2yr"]
        addr = r.get("address", "")[:20]
        print(f"  {r['distance_m']:>5}m | {r['name']:30s} | 2歳空き:{v2} | {s2_str:10s} | {prob['label']}")


def calc_probability(vacancy, min_score, my_score=20):
    if vacancy > 0 and min_score is None:
        return {"probability": 0.90, "label": "◎ 高い", "reason": "空きあり・前年全員内定"}
    if vacancy > 0 and min_score is not None:
        if my_score >= min_score:
            return {"probability": 0.75, "label": "○ やや高い", "reason": f"スコア{my_score}≥ボーダー{min_score}"}
        diff = min_score - my_score
        return {"probability": max(0.05, 0.5 - diff * 0.05), "label": "△ 厳しい", "reason": f"スコア{my_score}<ボーダー{min_score}"}
    if vacancy == 0 and min_score is None:
        return {"probability": 0.30, "label": "△ 途中空き待ち", "reason": "満席・前年全員内定"}
    if vacancy == 0 and min_score is not None:
        if my_score >= min_score:
            return {"probability": 0.20, "label": "▲ 空き待ち", "reason": "満席・スコアはクリア"}
        return {"probability": 0.05, "label": "× 厳しい", "reason": "満席・スコア不足"}
    return {"probability": 0.10, "label": "? 不明", "reason": "データ不足"}


if __name__ == "__main__":
    main()
