#!/usr/bin/env python3
"""
豊島区 保育園データ取得スクリプト
- 欠員状況PDF → パース → JSON
- オープンデータ(施設マスタ) → JSON
- 内定指数PDF → パース → JSON
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber
import requests

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# 豊島区の保育園関連URL
URLS = {
    "vacancy_page": "https://www.city.toshima.lg.jp/260/kosodate/kosodate/hoikuen/nyuen/2005011348.html",
    "vacancy_index_page": "https://www.city.toshima.lg.jp/260/kosodate/kosodate/hoikuen/nyuen/1809261402.html",
    "capacity_page": "https://www.city.toshima.lg.jp/452/kosodate/kosodate/hoikuen/006596.html",
    "admission_score_page": "https://www.city.toshima.lg.jp/260/kosodate/kosodate/hoikuen/nyuen/2007161135.html",
    "application_stats_page": "https://www.city.toshima.lg.jp/260/kosodate/kosodate/hoikuen/nyuen/2007161117.html",
    "open_data_csv": "https://www.opendata.metro.tokyo.lg.jp/toyoshima/R4_preschool.csv",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) hokatsu-map/1.0"
}


def fetch_pdf_links(page_url: str) -> list[str]:
    """ページからPDFリンクを抽出"""
    resp = requests.get(page_url, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    # PDFリンクを正規表現で抽出
    pdf_links = re.findall(r'href="([^"]*\.pdf)"', resp.text)
    base = page_url.rsplit("/", 1)[0]
    full_links = []
    for link in pdf_links:
        if link.startswith("http"):
            full_links.append(link)
        elif link.startswith("/"):
            full_links.append(f"https://www.city.toshima.lg.jp{link}")
        else:
            full_links.append(f"{base}/{link}")
    return full_links


def download_pdf(url: str, filename: str) -> Path:
    """PDFをダウンロード"""
    filepath = DATA_DIR / filename
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    filepath.write_bytes(resp.content)
    print(f"  Downloaded: {filepath} ({len(resp.content)} bytes)")
    return filepath


def parse_vacancy_pdf(filepath: Path) -> list[dict]:
    """欠員状況PDFをパースして園ごとの空き状況を抽出"""
    results = []
    current_category = "unknown"

    with pdfplumber.open(filepath) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            # カテゴリ判定
            if "区立保育園" in text:
                current_category = "区立保育園"
            elif "私立保育園" in text or "私立認可" in text:
                current_category = "私立保育園"
            elif "地域型" in text or "小規模" in text:
                current_category = "地域型保育"
            elif "認定こども園" in text:
                current_category = "認定こども園"

            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 8:
                        continue
                    # ヘッダー行をスキップ
                    if row[0] and ("No" in str(row[0]) or "園名" in str(row[1] or "")):
                        continue
                    # 番号が数字の行がデータ行
                    no = str(row[0] or "").strip()
                    if not no or not re.match(r"^\d+$", no):
                        continue

                    name = str(row[1] or "").strip().replace("\n", "")

                    # 年齢別空き: 0歳〜5歳
                    ages = {}
                    age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
                    for i, label in enumerate(age_labels):
                        val = str(row[2 + i] or "").strip() if 2 + i < len(row) else ""
                        if val in ("", "-", "−", "―", "×"):
                            ages[label] = 0
                        else:
                            try:
                                ages[label] = int(val)
                            except ValueError:
                                ages[label] = 0

                    # 所在地（最後の方のカラム）
                    address = ""
                    for cell in reversed(row):
                        cell_str = str(cell or "").strip()
                        if "豊島区" in cell_str or "東池袋" in cell_str or "南池袋" in cell_str:
                            address = cell_str.replace("\n", "")
                            break

                    results.append({
                        "no": int(no),
                        "name": name,
                        "category": current_category,
                        "vacancy": ages,
                        "total_vacancy": sum(ages.values()),
                        "address": address,
                    })

    return results


def parse_score_pdf(filepath: Path) -> list[dict]:
    """内定指数PDFをパースして園ごとの最低内定指数を抽出"""
    results = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
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

                    scores = {}
                    age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
                    for i, label in enumerate(age_labels):
                        val = str(row[2 + i] or "").strip() if 2 + i < len(row) else ""
                        if val in ("", "-", "−", "―"):
                            scores[label] = None  # 全員内定 or 募集なし
                        else:
                            try:
                                scores[label] = int(val)
                            except ValueError:
                                scores[label] = None

                    results.append({
                        "no": int(no),
                        "name": name,
                        "min_score": scores,
                    })

    return results


def fetch_open_data() -> list[dict]:
    """東京都オープンデータから豊島区の施設マスタを取得"""
    filepath = DATA_DIR / "toshima_preschool.xlsx"
    resp = requests.get(URLS["open_data_csv"], headers=HEADERS, timeout=60)
    resp.raise_for_status()
    filepath.write_bytes(resp.content)
    print(f"  Downloaded open data: {filepath} ({len(resp.content)} bytes)")

    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath)
        ws = wb.active
        headers_row = [str(cell.value or "") for cell in ws[1]]
        facilities = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            record = {}
            for i, val in enumerate(row):
                if i < len(headers_row):
                    record[headers_row[i]] = val
            if record.get("名称"):
                facilities.append({
                    "name": str(record.get("名称", "")),
                    "name_kana": str(record.get("名称_カナ", "")),
                    "type": str(record.get("種別", "")),
                    "address": str(record.get("住所", "")),
                    "lat": float(record["緯度"]) if record.get("緯度") else None,
                    "lng": float(record["経度"]) if record.get("経度") else None,
                    "phone": str(record.get("電話番号", "")),
                    "capacity": record.get("収容定員"),
                    "age_range": str(record.get("受入年齢", "")),
                    "hours": f"{record.get('開始時間', '')}-{record.get('終了時間', '')}",
                    "temp_care": str(record.get("一時預かりの有無", "")),
                    "url": str(record.get("URL", "")),
                })
        return facilities
    except Exception as e:
        print(f"  Warning: Could not parse open data: {e}")
        return []


# 自宅の座標
HOME_LAT = 35.7283
HOME_LNG = 139.7193

def haversine_distance(lat1, lng1, lat2, lng2):
    """2点間の距離(m)をハーバーサイン公式で計算"""
    import math
    R = 6371000  # 地球の半径(m)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calculate_admission_probability(vacancy: int, min_score: int | None, my_score: int = 20) -> dict:
    """入園確率を推定"""
    if vacancy > 0 and min_score is None:
        # 空きあり + 全員内定 = 高確率
        return {"probability": 0.90, "label": "◎ 高い", "reason": "空きあり・前年全員内定"}
    elif vacancy > 0 and min_score is not None:
        if my_score >= min_score:
            return {"probability": 0.75, "label": "○ やや高い", "reason": f"空きあり・スコア{my_score}≥ボーダー{min_score}"}
        else:
            diff = min_score - my_score
            prob = max(0.05, 0.5 - diff * 0.05)
            return {"probability": prob, "label": "△ 厳しい", "reason": f"空きあり・スコア{my_score}<ボーダー{min_score}"}
    elif vacancy == 0 and min_score is None:
        return {"probability": 0.30, "label": "△ 途中退園待ち", "reason": "現在満席・前年全員内定（途中空き可能性あり）"}
    elif vacancy == 0 and min_score is not None:
        if my_score >= min_score:
            return {"probability": 0.20, "label": "▲ 空き待ち", "reason": f"満席・スコアはクリア"}
        else:
            return {"probability": 0.05, "label": "× ほぼ無理", "reason": f"満席・スコア不足"}
    return {"probability": 0.10, "label": "? 不明", "reason": "データ不足"}


def main():
    print("=" * 60)
    print("豊島区 保育園データ取得・解析")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. 欠員状況PDF取得
    print("\n[1/4] 欠員状況PDFを取得中...")
    try:
        pdf_links = fetch_pdf_links(URLS["vacancy_page"])
        pdf_links += fetch_pdf_links(URLS["vacancy_index_page"])
        # 重複除去
        pdf_links = list(dict.fromkeys(pdf_links))
        print(f"  Found {len(pdf_links)} PDF links")

        vacancy_data = []
        for link in pdf_links:
            if "ketuin" in link.lower() or "欠員" in link or "ketuinn" in link.lower():
                filename = f"vacancy_{Path(link).name}"
                filepath = download_pdf(link, filename)
                parsed = parse_vacancy_pdf(filepath)
                if parsed:
                    vacancy_data = parsed
                    print(f"  Parsed {len(parsed)} facilities from vacancy PDF")
                    break

        if not vacancy_data:
            # 全PDFを試す
            for link in pdf_links:
                filename = f"pdf_{Path(link).name}"
                filepath = download_pdf(link, filename)
                parsed = parse_vacancy_pdf(filepath)
                if len(parsed) > 10:  # 保育園データっぽい
                    vacancy_data = parsed
                    print(f"  Parsed {len(parsed)} facilities")
                    break
    except Exception as e:
        print(f"  Error fetching vacancy data: {e}")
        vacancy_data = []

    # 2. 内定指数PDF取得
    print("\n[2/4] 内定指数PDFを取得中...")
    score_data = []
    try:
        pdf_links = fetch_pdf_links(URLS["admission_score_page"])
        print(f"  Found {len(pdf_links)} PDF links")
        for link in pdf_links:
            filename = f"score_{Path(link).name}"
            filepath = download_pdf(link, filename)
            parsed = parse_score_pdf(filepath)
            if parsed:
                score_data = parsed
                print(f"  Parsed {len(parsed)} facilities from score PDF")
                break
    except Exception as e:
        print(f"  Error fetching score data: {e}")

    # 3. オープンデータ取得
    print("\n[3/4] オープンデータ（施設マスタ）を取得中...")
    try:
        facilities = fetch_open_data()
        print(f"  Got {len(facilities)} facilities with coordinates")
    except Exception as e:
        print(f"  Error fetching open data: {e}")
        facilities = []

    # 4. データ統合
    print("\n[4/4] データ統合・確率計算中...")

    # 名前でマッチング（部分一致）
    def find_facility(name, facility_list):
        name_clean = name.replace(" ", "").replace("　", "")
        for f in facility_list:
            f_clean = f["name"].replace(" ", "").replace("　", "")
            if name_clean in f_clean or f_clean in name_clean:
                return f
            # 短い名前の部分一致
            if len(name_clean) > 3 and name_clean[:4] in f_clean:
                return f
        return None

    # スコアデータを名前でインデックス
    score_by_name = {}
    for s in score_data:
        score_by_name[s["name"]] = s

    integrated = []
    for v in vacancy_data:
        record = {**v}

        # 座標を付与
        fac = find_facility(v["name"], facilities)
        if fac:
            record["lat"] = fac["lat"]
            record["lng"] = fac["lng"]
            record["phone"] = fac.get("phone", "")
            record["capacity"] = fac.get("capacity")
            record["hours"] = fac.get("hours", "")
        else:
            record["lat"] = None
            record["lng"] = None

        # 自宅からの距離
        if record["lat"] and record["lng"]:
            record["distance_m"] = round(haversine_distance(
                HOME_LAT, HOME_LNG, record["lat"], record["lng"]
            ))
        else:
            record["distance_m"] = None

        # 内定指数を付与
        score_match = find_facility(v["name"], [{"name": s["name"], **s} for s in score_data])
        min_score_2 = None
        if score_match:
            record["min_scores"] = score_match.get("min_score", {})
            min_score_2 = record["min_scores"].get("2歳")
        else:
            record["min_scores"] = {}

        # 入園確率計算（2歳クラス）
        vacancy_2 = v["vacancy"].get("2歳", 0)
        record["admission_2yr"] = calculate_admission_probability(vacancy_2, min_score_2, my_score=20)

        integrated.append(record)

    # 自宅からの距離でソート
    integrated.sort(key=lambda x: x.get("distance_m") or 99999)

    # 保存
    output = {
        "generated_at": datetime.now().isoformat(),
        "home_location": {"lat": HOME_LAT, "lng": HOME_LNG},
        "my_score": 20,
        "total_facilities": len(integrated),
        "facilities": integrated,
    }

    output_path = DATA_DIR / "hokatsu_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  Saved: {output_path}")

    # サマリ出力
    print("\n" + "=" * 60)
    print("東池袋エリア 2歳クラス サマリ")
    print("=" * 60)
    nearby = [r for r in integrated if r.get("distance_m") and r["distance_m"] <= 1500]
    print(f"\n自宅から1.5km以内: {len(nearby)}園")
    for r in nearby:
        v2 = r["vacancy"].get("2歳", 0)
        prob = r["admission_2yr"]
        dist = r.get("distance_m", "?")
        print(f"  {r['name']:30s} | {dist:>5}m | 2歳空き:{v2} | {prob['label']} | {prob['reason']}")

    print(f"\n全{len(integrated)}園のデータを {output_path} に保存しました")
    return output


if __name__ == "__main__":
    main()

# ============================================================
# 新設予定の保育施設チェック
# ============================================================
# データ収集時に以下も確認すること:
# 1. 豊島区 認可保育施設の新規開設ページ
#    https://www.city.toshima.lg.jp/269/kosodate/kosodate/hoikuen/documents/2103121133.html
# 2. 周辺の再開発事業（豊島区 市街地再開発事業ページ）
#    https://www.city.toshima.lg.jp/300/machizukuri/toshikekaku/shigaichi/
# 3. 新築マンションの低層部に保育施設が入る予定がないか
#    - SUUMO/不動産サイトで「保育」「子育て」で検索
# 4. 渋谷区も同様
#    https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen-nyuen/hoiku_aki.html
