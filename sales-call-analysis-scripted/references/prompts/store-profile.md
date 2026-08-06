# 唯一任务：门店档案

生成本次录音的门店快照。最终展示固定为九个维度，不得增减：一句话画像、门店基本信息、主营品类与品牌、经营模式、选品偏好、利润偏好、合作偏好与排斥项、关键证据、档案口径。

模型只生成一句话画像和六个事实 section；关键证据与档案口径由脚本依据证据 ID 和记录来源确定性生成。六个 section 键必须齐全；未提及时 `content` 写 `未确认`、`state_type` 写 `未确认`、证据为空。

- 只写对下次销售仍有价值的信息，不整段复制其他分析字段。
- 一句话画像和事实章节只能由客户证据支持；销售提问、介绍和利润测算不能证明门店事实。
- 不推断极度焦虑、认真倾听、情绪激动等心理状态。
- 飞书行定位元数据不会作为客户事实输入，脚本会另行写入档案口径。
- 地址、区域和门店类型必须逐字出现在对应客户证据中。
- 不输出老板/关键联系人、运营与工具能力、合规与经营风险、销售接手建议、证据边界、需求明确度或当前执行状态等已删除维度。

```json
{
  "_scope": {},
  "store-profile": {
    "one_line": "",
    "one_line_evidence_ids": ["U0001"],
    "sections": {
      "basic": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []},
      "categories_brands": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []},
      "business_model": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []},
      "selection_motion": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []},
      "price_profit": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []},
      "cooperation_preferences": {"content": "", "state_type": "稳定档案|当前状态|未确认", "evidence_ids": []}
    }
  }
}
```
