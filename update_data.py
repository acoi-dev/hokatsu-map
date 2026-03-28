#!/usr/bin/env python3
"""
保育園データ更新スクリプト（豊島区 + 渋谷区）
- 4/1データ更新 & 予測答え合わせ対応
- バックアップ → 取得 → 差分比較 → 履歴更新
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: pip install beautifulsoup4 が必要です")
    sys.exit(1)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None
    print("WARNING: pdfplumber がないため豊島区PDF解析はスキップされます")

DATA_DIR = Path(__file__).parent / "data"
BACKUP_DIR = DATA_DIR / "backup"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) hokatsu-map/1.0"
}

# ========== 渋谷区 ==========

SHIBUYA_URL = "https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen-nyuen/hoiku_aki.html"

def fetch_shibuya_vacancy() -> list[dict]:
    """渋谷区の空き状況HTMLテーブルをスクレイピング"""
    print("\n[渋谷区] 空き状況ページを取得中...")
    resp = requests.get(SHIBUYA_URL, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    facilities = []
    tables = soup.find_all("table")
    print(f"  テーブル数: {len(tables)}")

    for table_idx, table in enumerate(tables):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # ヘッダー行から年齢列を特定
        header_cells = rows[0].find_all(["th", "td"])
        header_texts = [c.get_text(strip=True) for c in header_cells]

        # 施設名 + 年齢列のあるテーブルを探す
        age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
        has_ages = sum(1 for h in header_texts if any(a in h for a in age_labels))
        if has_ages < 3:
            continue

        # 年齢列のインデックスを特定
        age_indices = {}
        for i, h in enumerate(header_texts):
            for a in age_labels:
                if a in h:
                    age_indices[a] = i
                    break

        # 施設名列のインデックス（「施設名」「園名」を含む列）
        name_idx = 0
        for i, h in enumerate(header_texts):
            if "施設" in h or "園名" in h or "名称" in h:
                name_idx = i
                break

        print(f"  テーブル{table_idx}: ヘッダー={header_texts[:8]}, 年齢列={age_indices}")

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < max(age_indices.values(), default=0) + 1:
                continue

            name = cells[name_idx].get_text(strip=True).replace("\n", "")
            if not name or "合計" in name or "計" == name:
                continue
            # ヘッダー行スキップ
            if "施設" in name or "園名" in name:
                continue

            vacancy = {}
            for age, idx in age_indices.items():
                val = cells[idx].get_text(strip=True)
                if val in ("", "-", "−", "―", "×", "☓", "—"):
                    vacancy[age] = 0
                else:
                    try:
                        vacancy[age] = int(re.sub(r"[^\d\-]", "", val) or "0")
                    except ValueError:
                        vacancy[age] = 0

            if any(v != 0 for v in vacancy.values()) or len(vacancy) >= 3:
                facilities.append({
                    "name": name,
                    "vacancy": vacancy,
                    "table_idx": table_idx,
                })

    print(f"  取得園数: {len(facilities)}")
    return facilities


def update_shibuya_data(new_vacancy: list[dict]) -> dict:
    """渋谷区データを更新（既存データに新しい空き状況をマージ）"""
    data_path = DATA_DIR / "shibuya_data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        current = json.load(f)

    updated_count = 0
    not_found = []

    # 名前でマッチング
    for nv in new_vacancy:
        matched = False
        nv_clean = re.sub(r"[\s　]", "", nv["name"])
        for fac in current["facilities"]:
            fac_clean = re.sub(r"[\s　]", "", fac["name"])
            if nv_clean == fac_clean or nv_clean in fac_clean or fac_clean in nv_clean:
                fac["vacancy"] = nv["vacancy"]
                updated_count += 1
                matched = True
                break
        if not matched:
            not_found.append(nv["name"])

    current["generated_at"] = datetime.now().isoformat()
    print(f"  更新: {updated_count}園, 未マッチ: {len(not_found)}園")
    if not_found:
        print(f"    未マッチ: {not_found[:10]}")

    return current


# ========== 豊島区 ==========

TOSHIMA_VACANCY_PAGE = "https://www.city.toshima.lg.jp/260/kosodate/kosodate/hoikuen/nyuen/2005011348.html"

def fetch_toshima_pdf_links() -> list[str]:
    """豊島区の欠員状況ページからPDFリンクを取得"""
    print("\n[豊島区] 欠員状況ページを取得中...")
    resp = requests.get(TOSHIMA_VACANCY_PAGE, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"

    pdf_links = re.findall(r'href="([^"]*\.pdf)"', resp.text)
    full_links = []
    for link in pdf_links:
        if link.startswith("http"):
            full_links.append(link)
        elif link.startswith("/"):
            full_links.append(f"https://www.city.toshima.lg.jp{link}")
        else:
            base = TOSHIMA_VACANCY_PAGE.rsplit("/", 1)[0]
            full_links.append(f"{base}/{link}")

    # 重複除去
    full_links = list(dict.fromkeys(full_links))
    print(f"  PDFリンク: {len(full_links)}件")
    for link in full_links:
        print(f"    {link}")
    return full_links


def download_and_parse_toshima_pdf(url: str) -> list[dict]:
    """豊島区PDFをダウンロードしてパース"""
    if not pdfplumber:
        print("  pdfplumberがないためスキップ")
        return []

    filename = f"toshima_{Path(url).name}"
    filepath = DATA_DIR / filename
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    filepath.write_bytes(resp.content)
    print(f"  Downloaded: {filepath.name} ({len(resp.content)} bytes)")

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
                    ages = {}
                    age_labels = ["0歳", "1歳", "2歳", "3歳", "4歳", "5歳"]
                    for i, label in enumerate(age_labels):
                        val = str(row[2 + i] or "").strip() if 2 + i < len(row) else ""
                        if val in ("", "-", "−", "―", "×", "☓"):
                            ages[label] = 0
                        else:
                            try:
                                ages[label] = int(re.sub(r"[^\d\-]", "", val) or "0")
                            except ValueError:
                                ages[label] = 0

                    results.append({"name": name, "vacancy": ages})

    print(f"  パース結果: {len(results)}園")
    return results


def update_toshima_data(new_vacancy: list[dict]) -> dict:
    """豊島区データを更新"""
    data_path = DATA_DIR / "hokatsu_data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        current = json.load(f)

    updated_count = 0
    for nv in new_vacancy:
        nv_clean = re.sub(r"[\s　]", "", nv["name"])
        for fac in current["facilities"]:
            fac_clean = re.sub(r"[\s　]", "", fac["name"])
            if nv_clean == fac_clean or nv_clean in fac_clean or fac_clean in nv_clean:
                fac["vacancy"] = nv["vacancy"]
                updated_count += 1
                break

    current["generated_at"] = datetime.now().isoformat()
    print(f"  更新: {updated_count}園")
    return current


# ========== 履歴 & 予測比較 ==========

def update_history(history_path: Path, data: dict):
    """履歴データに新しいスナップショットを追加"""
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {}

    for fac in data.get("facilities", []):
        name = fac["name"]
        if name not in history:
            history[name] = []
        history[name].append(fac.get("vacancy", {}))

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"  履歴更新: {history_path.name}")


def compare_predictions(predictions_path: Path, actual_data: dict) -> str:
    """予測と実績を比較してレポートを生成"""
    try:
        with open(predictions_path, "r", encoding="utf-8") as f:
            pred_data = json.load(f)
    except FileNotFoundError:
        return "予測データなし"

    predictions = pred_data.get("predictions", pred_data)
    if "predictions" in pred_data:
        predictions = pred_data["predictions"]

    # 実績データをname→vacancyの辞書に
    actual = {}
    for fac in actual_data.get("facilities", []):
        actual[fac["name"]] = fac.get("vacancy", {})

    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append("予測 vs 実績 答え合わせ")
    report_lines.append(f"日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append("=" * 70)

    total_error = 0
    total_count = 0
    age_errors = {}
    perfect = 0
    close = 0  # ±1以内

    for name, pred_vacancy in predictions.items():
        # 名前マッチ
        act_vacancy = actual.get(name)
        if not act_vacancy:
            # ファジーマッチ
            name_clean = re.sub(r"[\s　]", "", name)
            for aname, avac in actual.items():
                if re.sub(r"[\s　]", "", aname) == name_clean or name_clean in re.sub(r"[\s　]", "", aname):
                    act_vacancy = avac
                    break

        if not act_vacancy:
            report_lines.append(f"\n{name}: 実績データなし")
            continue

        report_lines.append(f"\n{name}:")
        line_errors = []
        for age in ["0歳", "1歳", "2歳"]:
            p = pred_vacancy.get(age)
            a = act_vacancy.get(age)
            if p is not None and a is not None:
                error = abs(p - a)
                total_error += error
                total_count += 1
                if age not in age_errors:
                    age_errors[age] = []
                age_errors[age].append(error)
                if error == 0:
                    perfect += 1
                if error <= 1:
                    close += 1

                marker = "✅" if error == 0 else "⚠️" if error <= 1 else "❌"
                report_lines.append(f"  {age}: 予測={p} 実績={a} 差={p-a:+d} {marker}")
                line_errors.append(error)

    # サマリ
    report_lines.append("\n" + "=" * 70)
    report_lines.append("サマリ")
    report_lines.append("=" * 70)
    if total_count > 0:
        mae = total_error / total_count
        report_lines.append(f"平均絶対誤差 (MAE): {mae:.2f}枠")
        report_lines.append(f"完全一致: {perfect}/{total_count} ({perfect/total_count*100:.0f}%)")
        report_lines.append(f"±1以内: {close}/{total_count} ({close/total_count*100:.0f}%)")

        for age, errors in sorted(age_errors.items()):
            avg_e = sum(errors) / len(errors)
            report_lines.append(f"  {age}: MAE={avg_e:.2f} (n={len(errors)})")
    else:
        report_lines.append("比較データなし")

    report = "\n".join(report_lines)
    return report


# ========== メイン ==========

def backup_current():
    """現在のデータをバックアップ"""
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    for f in DATA_DIR.glob("*.json"):
        if f.parent == BACKUP_DIR:
            continue
        dest = BACKUP_DIR / f"{f.stem}_{ts}{f.suffix}"
        shutil.copy2(f, dest)
    print(f"バックアップ完了: {BACKUP_DIR}/")


def main():
    print("=" * 70)
    print("保育園データ更新（豊島区 + 渋谷区）")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    # バックアップ
    print("\n[0] バックアップ...")
    backup_current()

    # ===== 渋谷区 =====
    print("\n" + "=" * 70)
    print("渋谷区")
    print("=" * 70)

    shibuya_vacancy = fetch_shibuya_vacancy()
    if shibuya_vacancy:
        shibuya_data = update_shibuya_data(shibuya_vacancy)

        # 保存
        out_path = DATA_DIR / "shibuya_data.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(shibuya_data, f, ensure_ascii=False, indent=2)
        print(f"  保存: {out_path}")

        # 履歴更新
        update_history(DATA_DIR / "shibuya_history.json", shibuya_data)

        # 予測答え合わせ
        pred_path = DATA_DIR / "shibuya_predictions.json"
        if pred_path.exists():
            report = compare_predictions(pred_path, shibuya_data)
            print("\n" + report)
            # レポート保存
            report_path = DATA_DIR / f"prediction_report_{datetime.now().strftime('%Y%m%d')}.txt"
            report_path.write_text(report, encoding="utf-8")
            print(f"\n  レポート保存: {report_path}")
    else:
        print("  渋谷区データ取得失敗")

    # ===== 豊島区 =====
    print("\n" + "=" * 70)
    print("豊島区")
    print("=" * 70)

    pdf_links = fetch_toshima_pdf_links()
    toshima_vacancy = []
    for link in pdf_links:
        # 最新の欠員PDFを探す
        if "ketuin" in link.lower() or "ketuinn" in link.lower():
            parsed = download_and_parse_toshima_pdf(link)
            if len(parsed) > 10:
                toshima_vacancy = parsed
                break

    if not toshima_vacancy and pdf_links:
        # フォールバック: 全PDFを試す
        for link in pdf_links:
            parsed = download_and_parse_toshima_pdf(link)
            if len(parsed) > 10:
                toshima_vacancy = parsed
                break

    if toshima_vacancy:
        toshima_data = update_toshima_data(toshima_vacancy)
        out_path = DATA_DIR / "hokatsu_data.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(toshima_data, f, ensure_ascii=False, indent=2)
        print(f"  保存: {out_path}")
        update_history(DATA_DIR / "history.json", toshima_data)
    else:
        print("  豊島区データ取得失敗（PDFなし or pdfplumberなし）")

    print("\n" + "=" * 70)
    print("完了")
    print("=" * 70)


if __name__ == "__main__":
    main()
