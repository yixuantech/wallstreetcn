# 华尔街见闻 API 逆向结果

## 核心发现

华尔街见闻网站是 **Svelte SPA**，所有数据通过 `api-one-wscn.awtmt.com` 的API获取，**无需登录、无需签名、无需Token**，直接HTTP GET即可。

---

## API 1：早餐FM文章列表

```
GET https://api-one-wscn.awtmt.com/apiv1/content/information-flow?channel=breakfast&accept=article&cursor=&limit=20&action=upglide
Headers:
  Origin: https://wallstreetcn.com
  Referer: https://wallstreetcn.com/
```

**返回结构：**
```json
{
  "code": 20000,
  "data": {
    "next_cursor": "eyJ...",  // 分页游标
    "item_count": 20,
    "items": [{
      "resource_type": "article",
      "resource_owner": "inhouse",
      "resource": {
        "id": 3775660,
        "title": "华尔街见闻早餐FM-Radio | 2026年6月27日",
        "uri": "https://wallstreetcn.com/articles/3775660",
        "content_short": "五分钟看懂全球市场，尽在财经早餐。",
        "display_time": 1782516658,
        "is_priced": false,  // ⭐ 关键：false=免费，true=付费
        "vip_type": "",      // gold=需要黄金VIP
        "categories": [{"property_key": "global"}, {"property_key": "breakfast"}],
        "content_args": [...],  // 音频/图片资源
        "image": {"uri": "...", "height": 449, "width": 1024}
      }
    }]
  }
}
```

**分页：** 传 `cursor=上一次的next_cursor` 获取下一页

---

## API 2：文章详情（含正文）

```
GET https://api-one-wscn.awtmt.com/apiv1/content/articles/{id}?extract=0&accept_theme=theme%2Cpremium-theme&remove_disclaimer=1
Headers:
  Origin: https://wallstreetcn.com
  Referer: https://wallstreetcn.com/
```

**返回结构：**
```json
{
  "code": 20000,
  "data": {
    "id": 3775660,
    "title": "华尔街见闻早餐FM-Radio | 2026年6月27日",
    "content": "<h2>市场概述</h2><p>...</p>...",  // ⭐ HTML格式正文
    "is_priced": false,
    "vip_type": "",
    "audio_uri": "https://...",
    "author": {"display_name": "朱希"},
    "display_time": 1782516658,
    "comment_count": 0
  }
}
```

### ⚠️ 付费墙情况（已实测验证）

| 文章类型 | is_priced | vip_type | 内容长度 | 说明 |
|---------|-----------|----------|---------|------|
| 早餐FM-Radio | **false** | "" | ~7800字 | ✅ **完全免费，全文可获取** |
| 会员早报 | **true** | "gold" | ~916字 | ❌ 仅返回摘要，需VIP |

**结论：早餐FM-Radio 免费，内容完整可获取！会员早报被截断，需VIP。**

---

## API 3：市场实时行情

```
GET https://api-ddc-wscn.awtmt.com/market/real?prod_code={codes}&fields=prod_name,last_px,px_change,px_change_rate,price_precision,securities_type
Headers:
  Origin: https://wallstreetcn.com
  Referer: https://wallstreetcn.com/
```

**常用产品代码：**
- `DXY.OTC` - 美元指数
- `EURUSD.OTC` - 欧元/美元
- `USDJPY.OTC` - 美元/日元
- `XAUUSD.OTC` - 现货黄金
- `USCL.OTC` - WTI原油
- `USDCNH.OTC` - 离岸人民币

**返回示例：**
```json
{
  "data": {
    "fields": ["prod_name", "last_px", "px_change", "px_change_rate", ...],
    "snapshot": {
      "DXY.OTC": ["美元指数", 101.3551, -0.10, -0.099, "2", "forex"],
      "XAUUSD.OTC": ["现货黄金", 4089.26, 62.48, 1.55, "2", "commodity"]
    }
  }
}
```

---

## API 4：财经日历

```
GET https://api-one-wscn.awtmt.com/apiv1/finance/indicator/search?start_time={unix}&end_time={unix}&limit=3
```

---

## API 5：热门文章

```
GET https://api-one-wscn.awtmt.com/apiv1/content/articles/hot?period=all
```

---

## 爬取方案总结

### ✅ 推荐方案：直接调用API

**优势：**
1. **无需浏览器**：纯HTTP请求，速度快、资源占用低
2. **结构化数据**：JSON格式，解析简单可靠
3. **无需登录**：不需要Cookie/Token
4. **免费内容完整**：早餐FM-Radio全文可获取（~7800字）
5. **额外数据源**：市场行情API也可直接用

**流程：**
```
1. GET /apiv1/content/information-flow?channel=breakfast&limit=1
   → 获取最新早餐FM文章ID

2. GET /apiv1/content/articles/{id}
   → 获取完整正文（HTML）

3. (可选) GET /market/real?prod_code=...
   → 补充实时市场数据

4. HTML → 纯文本 → AI分析 → 报告
```

**风险：**
- API可能需要 `Origin`/`Referer` header（已验证必须）
- API路径可能随网站更新变化（概率低，模块化设计可快速修复）
- 会员早报内容被截断（但早餐FM-Radio免费够用）
