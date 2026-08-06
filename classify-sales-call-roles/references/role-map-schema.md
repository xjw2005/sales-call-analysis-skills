# 角色映射格式

## 支持的 ASR 输入

优先支持火山 LAS 返回结构：

```json
{
  "data": {
    "result": {
      "utterances": [
        {
          "text": "老板，我给您介绍一下合作方案。",
          "start_time": 1000,
          "end_time": 4200,
          "additions": {"speaker": "1"}
        }
      ]
    }
  }
}
```

也支持 utterance 数组。说话人字段可为 `additions.speaker`、`speaker_id`、`speaker`；时间字段可为 `start_time/end_time` 或 `start_ms/end_ms`。

## role-map.json

```json
{
  "default": {
    "0": "客户",
    "1": "销售",
    "2": "客户",
    "3": "销售",
    "4": "客户",
    "5": "旁人"
  },
  "segments": [
    {
      "start_ms": 2720000,
      "end_ms": null,
      "map": {
        "0": "旁人",
        "2": "旁人",
        "4": "旁人"
      }
    }
  ],
  "fallback": "旁人"
}
```

- `default`：整段录音的默认 speaker-to-role 映射。
- `segments`：可选。发言开始时间落在区间内时覆盖默认映射。
- `start_ms`：包含边界。
- `end_ms`：不包含边界；`null` 表示直到录音结束。
- `fallback`：未映射 speaker 的自动角色，只能是三种角色之一。
- segment 的 speaker 必须已在 `default` 出现；同一 speaker 的 segment 不得重叠。
- `default` 表示最早稳定业务场景，不表示 speaker ID 在整段录音中永远对应同一人。

没有时间段覆盖时，也可以直接使用简写：

```json
{"0":"客户","1":"销售","2":"旁人"}
```

## 映射后审计

`audit_role_map.py` 输出 `role-audit.json`：

```json
{
  "summary": {
    "conflict_windows": 2,
    "conflict_speakers": ["1"],
    "needs_repair": true,
    "severe": true
  },
  "conflicts": [
    {
      "speaker": "1",
      "window_start_ms": 900000,
      "window_end_ms": 1200000,
      "assigned_role": "客户",
      "proposed_role": "销售",
      "evidence": []
    }
  ]
}
```

- `needs_repair=true`：只针对列出的冲突窗口修复 role-map。
- 每轮修复后的 `conflict_windows` 必须下降，否则保留上一版映射。
- `severe=true` 且两轮仍未收敛时停止写回，标记 `role_error`。

## 文本输出

```text
[00:01.000–00:04.200] 销售：老板，我给您介绍一下合作方案。
[00:04.350–00:06.100] 客户：你先说一下具体政策。
```

录音超过一小时后，时间戳自动改为 `HH:MM:SS.mmm`。
