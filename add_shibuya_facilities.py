#!/usr/bin/env python3
"""
渋谷区の未登録園を追加するスクリプト
1. 公式ページから全園をスクレイピング（住所リンクも辿る）
2. 既存データにない園を特定
3. 住所→座標変換（国土地理院API）
4. shibuya_data.jsonに追加
"""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) hokatsu-map/1.0"}

SHIBUYA_URL = "https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen-nyuen/hoiku_aki.html"

# 渋谷区の保育施設一覧（住所が載ってるページ）
SHIBUYA_FACILITY_URLS = [
    # 区立保育園一覧
    "https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen/kuritsu-hoiku-list.html",
    # 私立保育園一覧
    "https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen/shiritsu-hoiku-list.html",
]

# 自宅の座標（渋谷区）
HOME_LAT = 35.6748
HOME_LNG = 139.6867

# 渋谷区の保育園住所（公式データより手動補完 + Google Maps確認済み）
# GSIジオコードで正確に取れないものを優先的に収録
KNOWN_ADDRESSES = {
    "渋谷保育園": "渋谷区渋谷1-18-9",
    "恵比寿保育園": "渋谷区恵比寿2-32-1",
    "新橋保育園": "渋谷区東2-21-12",
    "代官山保育園": "渋谷区代官山町14-10",
    "笹塚保育園": "渋谷区笹塚2-6-1",
    "大向保育園": "渋谷区神泉町12-7",
    "幡ヶ谷第二保育園": "渋谷区幡ヶ谷2-53-1",
    "氷川保育園": "渋谷区東2-20-18",
    "幡ヶ谷第三保育園": "渋谷区幡ヶ谷3-21-2",
    "広尾保育園": "渋谷区広尾2-1-25",
    "千駄ヶ谷保育園": "渋谷区千駄ヶ谷2-34-1",
    "本町第三保育園": "渋谷区本町4-7-4",
    "笹塚第二保育園": "渋谷区笹塚3-18-7",
    "山谷かきのみ園": "渋谷区鉢山町6-17",
    "鳩の森保育園": "渋谷区千駄ヶ谷4-29-12",
    "聖ヨゼフ保育園": "渋谷区広尾4-1-1",
    "広尾上宮保育園": "渋谷区広尾5-7-1",
    "保育園うさぎとかめ": "渋谷区恵比寿3-33-12",
    "保育園うさぎとかめHills": "渋谷区恵比寿3-30-12",
    "さくら上宮保育園": "渋谷区桜丘町26-1",
    "ベネッセ美竹の丘保育園": "渋谷区渋谷1-18-9",
    "まなびの森 渋谷もりのこ保育園神南": "渋谷区神南1-12-10",
    "キッズハーモニー・NEWoMan": "渋谷区千駄ヶ谷5-24-55",
    "キッズハーモニー・よよぎの杜": "渋谷区代々木1-2-8",
    "ほっぺるランド渋谷": "渋谷区渋谷3-11-2",
    "渋谷東ちとせ保育園": "渋谷区東1-3-10",
    "まなびの森保育園幡ヶ谷": "渋谷区幡ヶ谷1-23-3",
    "グローバルキッズ幡ヶ谷円": "渋谷区幡ヶ谷1-8-8",
    "さくらさくみらい笹塚": "渋谷区笹塚1-60-5",
    "美希保育園北参道": "渋谷区千駄ヶ谷3-41-2",
    "千駄ヶ谷りとるぱんぷきんず": "渋谷区千駄ヶ谷2-10-6",
    "ささはた保育園": "渋谷区笹塚1-30-1",
    "広尾ちとせ保育園": "渋谷区広尾4-1-21",
    "ポピンズナーサリースクール恵比寿南": "渋谷区恵比寿南3-1-24",
    "ChaCha Children Daikanyama": "渋谷区恵比寿西1-21-10",
    "未来のカプセル原宿保育園": "渋谷区神宮前3-17-14",
    "アソシエ神宮北参道保育園": "渋谷区千駄ヶ谷4-26-4",
    "神宮前あおぞらこども園": "渋谷区神宮前3-38-7",
    "本町きらきらこども園": "渋谷区本町3-47-14",
    "恵比寿のびのびこども園": "渋谷区恵比寿1-15-3",
    "本町そよかぜこども園": "渋谷区本町3-55-10",
    "渋谷東しぜんの国こども園": "渋谷区東3-24-12",
    "原宿こども園": "渋谷区神宮前3-36-23",
    "渋谷本町こども園": "渋谷区本町4-9-7",
    "プラスキッズ保育園・代々木上原": "渋谷区西原3-23-3",
    "シエル小規模保育園・恵比寿": "渋谷区恵比寿4-1-11",
    "ぬくもりのおうち保育 神宮園": "渋谷区神宮前2-14-16",
    "代官山恵比寿西保育室": "渋谷区恵比寿西2-8-17",
    "大山保育室": "渋谷区松濤2-14-12",
}


def haversine(lat1, lng1, lat2, lng2):
    import math
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# 渋谷区の中心座標（フォールバック検出用）
SHIBUYA_CENTER = (35.6637, 139.6977)

def geocode_gsi(address: str) -> tuple[float, float] | None:
    """国土地理院APIでジオコード（渋谷区中心へのフォールバック検出付き）"""
    try:
        query = address if "渋谷区" in address else f"東京都渋谷区{address}"
        resp = requests.get(
            f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={query}",
            timeout=10
        )
        results = resp.json()
        if results:
            lng, lat = results[0]["geometry"]["coordinates"]
            # 渋谷区役所付近(=フォールバック)の検出
            if abs(lat - SHIBUYA_CENTER[0]) < 0.001 and abs(lng - SHIBUYA_CENTER[1]) < 0.001:
                return None  # 具体的な場所が見つかっていない
            return lat, lng
    except Exception as e:
        print(f"    Geocode error: {e}")
    return None


def clean_name(name: str) -> str:
    """園名からノイズを除去"""
    name = re.sub(r"（外部サイト）", "", name)
    name = re.sub(r"\(外部サイト\)", "", name)
    name = name.strip()
    return name


def scrape_all_facilities():
    """渋谷区公式ページから全園の情報をスクレイピング"""
    print("[1] 渋谷区公式ページ取得中...")
    resp = requests.get(SHIBUYA_URL, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    all_facilities = []
    tables = soup.find_all("table")

    category_map = {
        0: "認可区立保育所",
        1: "認可私立保育所",
        2: "認可私立保育所",
        3: "認可私立保育所",
        4: "認定こども園",
        5: "小規模保育事業所",
        6: "区立保育室",
        7: "居宅訪問型保育",
    }

    for table_idx, table in enumerate(tables):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        header_texts = [c.get_text(strip=True) for c in header_cells]

        age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
        age_indices = {}
        for i, h in enumerate(header_texts):
            for a in age_labels:
                if a in h:
                    age_indices[a] = i
                    break

        if len(age_indices) < 3:
            continue

        # 施設名列を特定
        name_idx = None
        for i, h in enumerate(header_texts):
            if "施設" in h or "園名" in h or "名称" in h or "保育園" in h:
                name_idx = i
                break

        # 名前列が見つからないテーブルはスキップ（認定こども園・小規模のテーブルでヘッダーが数字になるケース）
        if name_idx is None:
            print(f"  テーブル{table_idx}: 園名列なし（ヘッダー: {header_texts[:6]}）→ スキップ")
            continue

        category = category_map.get(table_idx, "その他")

        ext_idx = None
        for i, h in enumerate(header_texts):
            if "延長" in h:
                ext_idx = i
                break

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < max(age_indices.values(), default=0) + 1:
                continue

            raw_name = cells[name_idx].get_text(strip=True).replace("\n", "").replace("　", " ").strip()
            name = clean_name(raw_name)

            if not name or len(name) < 2 or "合計" in name or name == "計" or "施設" in name or "園名" in name:
                continue
            # 数字だけの名前はパースエラー
            if re.match(r"^\d+$", name):
                continue

            vacancy = {}
            for age, idx in age_indices.items():
                if idx < len(cells):
                    val = cells[idx].get_text(strip=True)
                    if val in ("", "-", "−", "―", "×", "☓", "—"):
                        vacancy[age] = 0
                    else:
                        try:
                            vacancy[age] = int(re.sub(r"[^\d\-]", "", val) or "0")
                        except ValueError:
                            vacancy[age] = 0

            extended_hours = ""
            if ext_idx and ext_idx < len(cells):
                extended_hours = cells[ext_idx].get_text(strip=True)

            all_facilities.append({
                "name": name,
                "vacancy": vacancy,
                "category": category,
                "extended_hours": extended_hours,
            })

    print(f"  全{len(all_facilities)}園取得")
    return all_facilities


def fuzzy_match(name, existing_names):
    """名前のファジーマッチ"""
    clean = re.sub(r"[\s　・（）()「」]", "", name)
    for en in existing_names:
        en_clean = re.sub(r"[\s　・（）()「」]", "", en)
        if clean == en_clean or clean in en_clean or en_clean in clean:
            return en
    return None


def find_known_address(name: str) -> str:
    """既知の住所辞書から住所を検索（部分一致）"""
    # 完全一致
    if name in KNOWN_ADDRESSES:
        return KNOWN_ADDRESSES[name]
    # 括弧等を除去して再検索
    clean = re.sub(r"（.*?）|\(.*?\)", "", name).strip()
    if clean in KNOWN_ADDRESSES:
        return KNOWN_ADDRESSES[clean]
    # 部分一致
    for known_name, addr in KNOWN_ADDRESSES.items():
        if known_name in name or name in known_name:
            return addr
    return ""


def main():
    data_path = DATA_DIR / "shibuya_data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing_names = [f["name"] for f in data["facilities"]]
    print(f"既存: {len(existing_names)}園")

    all_facs = scrape_all_facilities()

    # 未登録園を特定 & 既存園を更新
    new_facs = []
    updated = 0
    for fac in all_facs:
        match = fuzzy_match(fac["name"], existing_names)
        if match:
            for ef in data["facilities"]:
                if ef["name"] == match:
                    ef["vacancy"] = fac["vacancy"]
                    updated += 1
                    break
        else:
            new_facs.append(fac)

    print(f"  既存更新: {updated}園")
    print(f"\n[2] 未登録園: {len(new_facs)}園")

    if not new_facs:
        print("追加する園はありません")
        return

    print(f"\n[3] ジオコーディング...")
    added = 0
    failed_geocode = []

    for nf in new_facs:
        name = nf["name"]
        address = find_known_address(name)

        coords = None
        if address:
            coords = geocode_gsi(address)
            time.sleep(0.2)

        if not coords:
            # 園名でジオコード試行
            coords = geocode_gsi(f"渋谷区 {name}")
            time.sleep(0.2)

        if not coords and address:
            # Nominatimフォールバック
            try:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": f"東京都{address}", "format": "json", "limit": 1, "countrycodes": "jp"},
                    headers={"User-Agent": "hokatsu-map/1.0"},
                    timeout=10
                )
                results = resp.json()
                if results:
                    coords = (float(results[0]["lat"]), float(results[0]["lon"]))
                time.sleep(1.1)  # Nominatim rate limit
            except Exception:
                pass

        lat, lng = coords if coords else (None, None)
        distance_m = haversine(HOME_LAT, HOME_LNG, lat, lng) if lat else None
        walk_min = round(distance_m / 80) if distance_m else None

        new_entry = {
            "name": name,
            "address": address,
            "lat": lat,
            "lng": lng,
            "phone": "",
            "capacity": None,
            "category": nf["category"],
            "vacancy": nf["vacancy"],
            "min_scores": {},
            "min_score_notes": {},
            "extended_hours": nf.get("extended_hours", ""),
            "has_garden": None,
            "garden_note": "",
            "cap_by_age": {},
            "website": "",
            "distance_m": distance_m,
            "walk_min": walk_min,
            "total_vacancy": sum(nf["vacancy"].values()),
        }

        data["facilities"].append(new_entry)
        added += 1

        if lat:
            print(f"  ✅ {name} → {address} ({lat:.4f}, {lng:.4f}) {distance_m}m")
        else:
            failed_geocode.append(name)
            print(f"  ⚠️ {name} → {address or '住所なし'} → 座標取得失敗")

    # 距離でソート（座標なしは末尾）
    data["facilities"].sort(key=lambda x: x.get("distance_m") or x.get("distance_home") or 99999)
    data["total_facilities"] = len(data["facilities"])
    data["generated_at"] = __import__("datetime").datetime.now().isoformat()

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n[4] 保存完了: {data_path}")
    print(f"  全{len(data['facilities'])}園（既存{len(existing_names)} + 新規{added}）")
    if failed_geocode:
        print(f"  座標なし: {len(failed_geocode)}園")
        for n in failed_geocode:
            print(f"    - {n}")


if __name__ == "__main__":
    main()
