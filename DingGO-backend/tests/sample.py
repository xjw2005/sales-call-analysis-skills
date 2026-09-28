"""测试用分析结果（结构同小程序 model/analysis.js 的首访样例，精简版）"""

TRANSCRIPT = [
    {"uid": "U0001", "role": "销售", "startMs": 2000, "text": "老板您好，我是 A2 奶粉的业务。"},
    {"uid": "U0002", "role": "客户", "startMs": 11000, "text": "都说控价，网上一比便宜几十。"},
]

ANALYSIS = {
    "explicitNeeds": [{"point": "控价要真正落地", "scene": "谈到线上比价", "explanation": "客户担心乱价", "evidence": [TRANSCRIPT[1]]}],
    "implicitNeeds": [],
    "concerns": [{"name": n, "state": "是" if n in ("控价防窜", "利润空间") else "否", "evidence": []}
                 for n in ["价格敏感", "物流时效", "控价防窜", "培训支持", "售后保障", "合作模式", "利润空间", "系统工具", "其他"]],
    "effectiveness": {"total": 71, "dims": [
        {"name": "开场目标与议程", "max": 10, "score": 8}, {"name": "需求挖掘", "max": 20, "score": 14},
        {"name": "问题影响与经济意义", "max": 20, "score": 6}, {"name": "倾听与异议承接", "max": 15, "score": 12},
    ], "conclusion": "整体推进较顺。", "outcome": "约定复访"},
    "quotes": [],
    "nextAction": {"judgement": "触发", "reason": "客户索要资料", "confirmed": ["周五发价格表"], "revisitValue": "高", "actions": [
        {"topic": "补齐控价证据", "owner": "销售", "timeframe": "明天", "action": "发控价案例", "reason": "信任度低", "acceptance": "客户回复"},
        {"topic": "复访促成试销", "owner": "销售", "timeframe": "下周二", "action": "带试销协议到店", "reason": "可退货", "acceptance": "签试销单"},
    ]},
    "profile": {"sections": [
        {"key": "one_line", "label": "一句话画像", "state": "当前状态", "content": "社区母婴店，担心线上比价。"},
        {"key": "business_model", "label": "经营模式", "state": "未确认", "content": "未确认"},
    ]},
}
