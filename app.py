import streamlit as st
import pandas as pd
import time
import random
from datetime import datetime
import pytz
import requests
import json
import os

# エラー回避のため、安全にインポート
try:
    from sp_api.base import SellingApiRequestThrottledException
except ImportError:
    SellingApiRequestThrottledException = Exception

from sp_api.api import CatalogItems, Products, ProductFees
from sp_api.base import Marketplaces

# --- 1. ページ設定（タイトル修正） ---
st.set_page_config(
    page_title="Amazon SP-API 商品リサーチツール (ライフスタイル様専用)", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- 2. UI非表示用CSS（メニュー、Github、Footer等を消す） ---
hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            .stDeployButton {display:none;}
            [data-testid="stToolbar"] {visibility: hidden !important;}
            [data-testid="stDecoration"] {display: none;}
            [data-testid="stStatusWidget"] {visibility: hidden;}
            </style>
            """
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# --- 3. 認証機能（ID/PASS修正） ---
def check_password():
    """簡易ログイン機能"""
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    st.markdown("## 🔐 ログイン")
    
    col1, col2 = st.columns([1, 2])
    with col1:
        user_id = st.text_input("ユーザーID", key="login_user")
        password = st.text_input("パスワード", type="password", key="login_pass")
        
        if st.button("ログイン"):
            # 指定されたIDとパスワード
            ADMIN_USER = "smuggler"
            ADMIN_PASS = "1793-2565-4"
            
            if user_id == ADMIN_USER and password == ADMIN_PASS:
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("IDまたはパスワードが間違っています")
    return False

# --- ユーティリティ関数 ---
def calculate_shipping_fee(height, length, width):
    try:
        h, l, w = float(height), float(length), float(width)
        total_size = h + l + w
        if h <= 3 and total_size < 60: return 290
        elif total_size <= 60: return 580
        elif total_size <= 80: return 670
        elif total_size <= 100: return 780
        elif total_size <= 120: return 900
        elif total_size <= 140: return 1050
        elif total_size <= 160: return 1300
        elif total_size <= 170: return 2000
        elif total_size <= 180: return 2500
        elif total_size <= 200: return 3000
        else: return 'N/A'
    except: return 'N/A'

# --- Keepa連携 ---
class SellerNameResolver:
    def __init__(self, keepa_key=None):
        self.keepa_key = keepa_key
        self.file_path = 'sellers.json'
        self.seller_map = self._load_map()

    def _load_map(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return {}
        return {}

    def _save_map(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.seller_map, f, ensure_ascii=False, indent=2)
        except: pass

    def get_name(self, seller_id):
        if not seller_id: return "Unknown"
        if seller_id == 'AN1VRQENFRJN5': return 'Amazon.co.jp'
        if seller_id in self.seller_map: return self.seller_map[seller_id]
        
        if self.keepa_key:
            try:
                url = f"https://api.keepa.com/seller?key={self.keepa_key}&domain=5&seller={seller_id}"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if 'sellers' in data and data['sellers']:
                        seller_data = data['sellers'].get(seller_id, {})
                        seller_name = seller_data.get('sellerName')
                        if seller_name:
                            self.seller_map[seller_id] = seller_name
                            self._save_map()
                            return seller_name
            except: pass
        return seller_id

# --- SP-API ロジック ---
class AmazonSearcher:
    def __init__(self, credentials, keepa_key=None):
        self.credentials = credentials
        self.marketplace = Marketplaces.JP
        self.mp_id = 'A1VC38T7YXB528'
        self.logs = [] 
        self.resolver = SellerNameResolver(keepa_key)

    def log(self, message):
        ts = datetime.now().strftime('%H:%M:%S')
        self.logs.append(f"[{ts}] {message}")

    def _call_api_safely(self, func, **kwargs):
        retries = 5
        base_delay = 2.0 
        for i in range(retries):
            try:
                return func(**kwargs)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "Throttled" in error_str or "QuotaExceeded" in error_str:
                    wait_time = base_delay * (i + 1) + random.uniform(0.5, 1.5)
                    # ログ記録は内部で行うが、画面には出さない
                    # self.log(f"Wait: {wait_time:.1f}s") 
                    time.sleep(wait_time)
                else:
                    self.log(f"API Error: {error_str}")
                    return None
        return None

    def get_product_details_accurate(self, asin):
        # 1. Catalog
        catalog = CatalogItems(credentials=self.credentials, marketplace=self.marketplace)
        res_cat = self._call_api_safely(
            catalog.get_catalog_item, asin=asin, marketplaceIds=[self.mp_id],
            includedData=['attributes', 'salesRanks', 'summaries']
        )
        
        info = {
            'asin': asin, 'jan': '', 'title': '', 'brand': '', 'category': '',
            'rank': 999999, 'rank_disp': '', 'price': 0, 'price_disp': '-',
            'points': '', 'fee_rate': '', 'seller': '', 'size': '', 'shipping': ''
        }
        list_price = 0

        if res_cat and res_cat.payload:
            data = res_cat.payload
            if 'summaries' in data: info['title'] = data['summaries'][0].get('itemName', '')
            if 'summaries' in data: info['brand'] = data['summaries'][0].get('brandName', '')
            
            if 'attributes' in data:
                attrs = data['attributes']
                if 'externally_assigned_product_identifier' in attrs:
                    for ext in attrs['externally_assigned_product_identifier']:
                        if ext.get('type') == 'ean': info['jan'] = ext.get('value', '')
                if 'list_price' in attrs:
                    for lp in attrs['list_price']:
                        if lp.get('currency') == 'JPY': list_price = lp.get('value', 0)
                if 'item_package_dimensions' in attrs:
                    dim = attrs['item_package_dimensions'][0]
                    h = (dim.get('height') or {}).get('value', 0)
                    l = (dim.get('length') or {}).get('value', 0)
                    w = (dim.get('width') or {}).get('value', 0)
                    info['size'] = f"{h}x{l}x{w}"
                    s_fee = calculate_shipping_fee(h, l, w)
                    info['shipping'] = f"¥{s_fee}" if s_fee != 'N/A' else '-'
            if 'salesRanks' in data:
                ranks = data['salesRanks'][0].get('ranks', [])
                if ranks:
                    info['category'] = ranks[0].get('title', '')
                    info['rank'] = ranks[0].get('rank', 999999)
                    info['rank_disp'] = f"{info['rank']}位"

        # 2. Products
        products_api = Products(credentials=self.credentials, marketplace=self.marketplace)
        time.sleep(1.5)
        res_offers = self._call_api_safely(
            products_api.get_item_offers, asin=asin, MarketplaceId=self.mp_id, item_condition='New'
        )

        price_found = False
        if res_offers and res_offers.payload and 'Offers' in res_offers.payload:
            target_offer = None
            for offer in res_offers.payload['Offers']:
                if offer.get('IsBuyBoxWinner', False):
                    target_offer = offer
                    break
            if not target_offer:
                best_p = float('inf')
                for offer in res_offers.payload['Offers']:
                    p = (offer.get('ListingPrice') or {}).get('Amount', 0)
                    s = (offer.get('Shipping') or {}).get('Amount', 0)
                    total = p + s
                    if total > 0 and total < best_p:
                        best_p = total
                        target_offer = offer
            
            if target_offer:
                p = (target_offer.get('ListingPrice') or {}).get('Amount', 0)
                s = (target_offer.get('Shipping') or {}).get('Amount', 0)
                total_price = p + s
                pt_data = target_offer.get('Points', {})
                points = pt_data.get('PointsNumber', 0)
                
                if total_price > 0:
                    info['price'] = total_price
                    info['price_disp'] = f"¥{total_price:,.0f}"
                    if points > 0: info['points'] = f"{(points/total_price)*100:.1f}%"
                    
                    seller_id = target_offer.get('SellerId', '')
                    info['seller'] = self.resolver.get_name(seller_id)
                    price_found = True

        if not price_found and list_price > 0:
            info['price_disp'] = f"¥{list_price:,.0f} (参考)"
            info['seller'] = 'Ref Only'

        # 3. Fees
        if info['price'] > 0:
            time.sleep(0.5)
            fees_api = ProductFees(credentials=self.credentials, marketplace=self.marketplace)
            res_fee = self._call_api_safely(
                fees_api.get_product_fees_estimate_for_asin,
                asin=asin, price=info['price'], is_fba=True, identifier=f'fee-{asin}', currency='JPY', marketplace_id=self.mp_id
            )
            if res_fee and res_fee.payload:
                fees = res_fee.payload.get('FeesEstimateResult', {}).get('FeesEstimate', {}).get('FeeDetailList', [])
                for fee in fees:
                    if fee.get('FeeType') == 'ReferralFee':
                        amt = (fee.get('FinalFee') or {}).get('Amount', 0)
                        if amt > 0: info['fee_rate'] = f"{(amt/info['price'])*100:.1f}%"

        return info

    def search_by_keywords(self, keywords, max_results):
        catalog = CatalogItems(credentials=self.credentials, marketplace=self.marketplace)
        found_items = []
        page_token = None
        scan_limit = int(max_results * 1.5)
        if scan_limit < 20: scan_limit = 20

        while len(found_items) < scan_limit:
            params = {'keywords': [keywords], 'marketplaceIds': [self.mp_id], 'includedData': ['salesRanks'], 'pageSize': 20}
            if page_token: params['pageToken'] = page_token
            res = self._call_api_safely(catalog.search_catalog_items, **params)
            if res and res.payload:
                items = res.payload.get('items', [])
                if not items: break
                for item in items:
                    asin = item.get('asin')
                    rank_val = 9999999 
                    if 'salesRanks' in item and item['salesRanks']:
                        ranks = item['salesRanks'][0].get('ranks', [])
                        if ranks: rank_val = ranks[0].get('rank', 9999999)
                    found_items.append({'asin': asin, 'rank': rank_val})
                page_token = res.next_token
                if not page_token: break
            else: break
            time.sleep(1)
        
        sorted_items = sorted(found_items, key=lambda x: x['rank'])
        return [item['asin'] for item in sorted_items][:max_results]

    def search_by_jan(self, jan_code):
        catalog = CatalogItems(credentials=self.credentials, marketplace=self.marketplace)
        res = self._call_api_safely(catalog.search_catalog_items, keywords=[jan_code], marketplaceIds=[self.mp_id])
        if res and res.payload and 'items' in res.payload:
            items = res.payload['items']
            if items: return items[0].get('asin')
        return None

# --- Main App ---
def main():
    if not check_password(): return

    # ヘッダータイトル
    st.title("📦 Amazon SP-API 商品リサーチツール (ライフスタイル様専用 made by 岡田屋)")

    # ★サイドバーのAPIキー設定欄を削除し、Secretsから読み込みだけ行う
    if "LWA_APP_ID" not in st.secrets:
        st.error("システムエラー: API認証情報が設定されていません。管理者に連絡してください。")
        return

    # Secretsから読み込み
    credentials = {
        'refresh_token': st.secrets["REFRESH_TOKEN"],
        'lwa_app_id': st.secrets["LWA_APP_ID"],
        'lwa_client_secret': st.secrets["LWA_CLIENT_SECRET"],
        'aws_access_key': st.secrets["AWS_ACCESS_KEY"],
        'aws_secret_key': st.secrets["AWS_SECRET_KEY"],
        'role_arn': st.secrets.get("ROLE_ARN", "")
    }
    keepa_key = st.secrets.get("KEEPA_API_KEY", "")

    # 検索条件の入力欄のみ表示
    st.markdown("### 🔍 検索条件")
    col_mode, col_limit = st.columns([2, 1])
    with col_mode:
        search_mode = st.selectbox("検索モード", ["JANコードリスト", "ASINリスト", "ブランド検索", "カテゴリ/キーワード検索"])
    with col_limit:
        max_results = st.slider("取得件数上限", 10, 200, 50, 10)

    input_data = ""
    if search_mode in ["JANコードリスト", "ASINリスト"]:
        input_data = st.text_area(f"{search_mode} (1行に1つ)", height=150)
    else:
        input_data = st.text_input(f"{search_mode} キーワード")

    if st.button("検索開始", type="primary"):
        searcher = AmazonSearcher(credentials, keepa_key=keepa_key)
        target_asins = []
        progress_bar = st.progress(0)
        status_text = st.empty()

        status_text.info("リスト作成中...")
        if search_mode == "JANコードリスト":
            jan_list = [line.strip() for line in input_data.split('\n') if line.strip()]
            for i, jan in enumerate(jan_list):
                status_text.text(f"JAN変換: {jan}")
                asin = searcher.search_by_jan(jan)
                if asin: target_asins.append(asin)
                time.sleep(0.5)
                progress_bar.progress((i+1)/len(jan_list)*0.3)
        elif search_mode == "ASINリスト":
            target_asins = [line.strip() for line in input_data.split('\n') if line.strip()]
            progress_bar.progress(30)
        else:
            target_asins = searcher.search_by_keywords(input_data, max_results)
            progress_bar.progress(30)

        if not target_asins:
            st.error("商品が見つかりません")
            with st.expander("エラー詳細"):
                for log in searcher.logs: st.text(log)
            return

        st.success(f"{len(target_asins)}件のASINを特定。詳細情報を取得します...")
        
        results = []
        df_placeholder = st.empty()
        
        for i, asin in enumerate(target_asins):
            status_text.text(f"詳細取得中 ({i+1}/{len(target_asins)}): {asin}")
            detail = searcher.get_product_details_accurate(asin)
            if detail: results.append(detail)
            
            if results:
                df = pd.DataFrame(results)
                disp = {
                    'title':'商品名', 'brand':'ブランド', 'price_disp':'価格', 
                    'rank_disp':'ランキング', 'category':'カテゴリ',
                    'points':'ポイント率', 'fee_rate':'手数料率', 'seller': 'セラー', 'asin':'ASIN'
                }
                cols = [c for c in disp.keys() if c in df.columns]
                df_placeholder.dataframe(df[cols].rename(columns=disp), use_container_width=True)

            progress_bar.progress(min(((i+1)/len(target_asins)), 1.0))

        status_text.success("完了！")
        progress_bar.progress(100)

        # クライアント向けにはデバッグログは隠す（Expanderだけ残す）
        # with st.expander("ログ"): ... も削除して見た目を完全にクリーンにする場合は削除可

        if results:
            df_final = pd.DataFrame(results)
            df_final = df_final.drop(columns=['rank', 'price'], errors='ignore')
            jst = pytz.timezone('Asia/Tokyo')
            fname = f"amazon_research_{datetime.now(jst).strftime('%Y%m%d_%H%M%S')}.csv"
            st.download_button("📥 CSVダウンロード", df_final.to_csv(index=False).encode('utf-8_sig'), fname, "text/csv", type="primary")

if __name__ == "__main__":
    main()
