def search_by_keywords(self, keywords, max_results):
        """キーワード検索（エラー表示強化版）"""
        catalog = CatalogItems(credentials=self.credentials, marketplace=self.marketplace)
        found_items = []
        page_token = None
        status_text = st.empty()
        
        scan_limit = int(max_results * 1.5)
        if scan_limit < 20: scan_limit = 20

        while len(found_items) < scan_limit:
            params = {
                'keywords': [keywords], 'marketplaceIds': [self.mp_id],
                'includedData': ['salesRanks'], 'pageSize': 20
            }
            if page_token: params['pageToken'] = page_token

            try:
                # ★変更点: エラーを隠さずキャッチして表示する
                res = catalog.search_catalog_items(**params)
                
                if res and res.payload:
                    items = res.payload.get('items', [])
                    if not items: break
                    for item in items:
                        asin = item.get('asin')
                        rank_val = 9999999 
                        if 'salesRanks' in item and item['salesRanks']:
                            ranks_list = item['salesRanks'][0].get('ranks', [])
                            if ranks_list: rank_val = ranks_list[0].get('rank', 9999999)
                        found_items.append({'asin': asin, 'rank': rank_val})
                    page_token = res.next_token
                    if not page_token: break
                else: break
                time.sleep(1)
            
            except Exception as e:
                # ★ここでエラーを画面にドーンと出す
                st.error(f"検索APIエラー発生: {str(e)}")
                self.log(f"Search Error: {str(e)}")
                break
        
        sorted_items = sorted(found_items, key=lambda x: x['rank'])
        return [item['asin'] for item in sorted_items][:max_results]
