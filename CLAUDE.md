# hokatsu-map

保活マップ: 保育園データの収集・分析・可視化プロジェクト

## データ構造

- `data/hokatsu_data.json` - 豊島区の保育園データ
- `data/shibuya_data.json` - 渋谷区の保育園データ
- `data/capacity_data.json` - 定員データ
- `data/history.json` - 履歴データ
- `data/nursery_details.json` - 園の詳細情報
- `data/nursery_reviews.json` - 口コミ
- `data/nursery_analysis.json` - 分析結果

## JSONフォーマット (shibuya_data.json)

```json
{
  "home_location": {"lat": 35.6748, "lng": 139.6867},
  "scoring_system": { ... },
  "facilities": [
    {
      "name", "address", "lat", "lng", "phone",
      "capacity", "category",
      "vacancy": {"0歳": N, "1歳": N, ...},
      "min_scores": {"0歳": N, "1歳": N, ...},
      "min_score_notes": {"0歳": "44pt", "1歳": "46pt兄弟同園", ...},
      "extended_hours", "has_garden", "garden_note",
      "cap_by_age": {"0歳": "N", ...},
      "distance_m", "walk_min",
      "website", "google_maps_url"
    }
  ]
}
```

## 渋谷区の指数

- 基本指数: フルタイム=20pt/人、父母合算で40pt
- 実質ボーダー: 44pt（フルタイム40pt + 調整加点）
- 人気園は46pt（受託・兄弟同園加点）が必要
- 調整指数22項目、加点最大+6、減点最大-40

## ビジュアライゼーション

- `dashboard.html` / `dashboard_v2.png` - ダッシュボード
- `matrix.html` / `matrix_v5.png` - マトリクス分析
- `list.html` - リスト表示
- `nursery-analysis.html` - 分析ページ

## 競合分析

- `competitive-analysis.html` - 保活サービス競合分析レポート（2026/3/20作成）
- 主要競合: ホイシル(コドモン)、えんさがそっ♪(BABY JOB)、minkou、ここdeサーチ
- 市場ギャップ: リアルタイム空き状況、入園可能性AI判定、汎用指数計算は全サービスが未実装
- 2強: ホイシル(68,163施設) + えんさがそっ♪(40万MAU) が東京都ワンストップ公式パートナー
- ママスタ保活は2025年3月サービス終了

## マネタイズ分析

- `monetization-analysis.html` - 7モデル比較マネタイズ徹底研究（2026/3/20作成）
- 最推奨: 保活コンサル（ココナラ出品、即日開始可、月5-30万円）
- 基盤収入: 広告モデル（AdSense + アフィリエイト、SEO集客が生命線）
- 中期有望: データAPI販売（空き状況・ボーダーラインは独自データ、不動産テック向け）
- Exit: M&A（MAU 10万+ / 全国データで50-500万円の売却レンジ）
- 非推奨: フリーミアム（保活ユーザーに課金文化なし）、自治体SaaS（個人には非現実的）
- 教訓: ママスタ保活（東証プライム・インタースペース）が6年運営後に撤退。園検索単体では稼げない
- 成功モデル: コドモン(ICT SaaS) / BABY JOB(おむつサブスク) → 検索は集客チャネル、別で課金

## データソース

- 渋谷区: https://www.city.shibuya.tokyo.jp/kodomo/hoiku/hoikuen-nyuen/hoiku_aki.html
- 豊島区: 別途
