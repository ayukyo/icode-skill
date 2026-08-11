# tools/ppt - PPT 生成引擎（`/icode ppt` 独立交付步骤的底层工具）

`/icode ppt` 的生成层：**模板文字替换式**产出真实 `.pptx`。机制来自
[lmori1301/Agent-PPTSkill](https://github.com/lmori1301/Agent-PPTSkill)（脚本 MIT；**模板非商业**，见 [NOTICE](NOTICE)）。

## 文件

| 文件/目录 | 职责 |
|------|------|
| `scripts/build_pptx.py` | 按 `edits.json`（选页 + 文字替换）从模板生成 .pptx；带出框检测（默认只提示不阻断，`--strict` 时超框拒绝保存） |
| `scripts/render_slides.py` | .pptx → PDF → 每页 PNG（LibreOffice + pdftoppm，渲染自检用，可选） |
| `scripts/compute_capacity.py` | 由 template.pptx 计算每个 slot 容量字段（数据准备/自建模用） |
| `templates/INDEX.md` | 16 套内置模板索引（风格/主色/场景/页数） |
| `templates/<slug>/` | 每套 4 文件：`template.pptx` / `intro.md` / `detail.json`（槽位寻址+容量+type_scale）/ `preview.png` |

## 前置

```bash
python3 -c "import pptx"      # python-pptx 1.0+（必需，缺则 pip install python-pptx）
soffice --version              # LibreOffice（仅 render_slides.py 预览需要）
which pdftoppm              # poppler（仅 render_slides.py 预览需要）
```

**中文字体**：模板用「微软雅黑」；机器无此字体时渲染预览缺字，可配 fontconfig alias：

```
<alias binding="strong">
  <family>微软雅黑</family>
  <accept>
    <family>WenQuanYi Micro Hei</family>
    <family>DengXian</family>
    <family>Noto Sans SC</family>
  </accept>
</alias>
```

## 用法（被 `/icode ppt` 步骤调用）

```bash
TPL=tools/ppt/templates/minimal-business-summary

# 选模板（INDEX.md）→ 读 intro.md + detail.json → 生成 .icode_output/ppt/{场景关键词}_edits.json：
# { "template_slug": "...", "selected_slides": [1,2,3],
#   "edits": [ {"slide": 1, "slot_id": "cover_title_cn", "new_text": "..."} ] }

mkdir -p .icode_output/ppt
python3 tools/ppt/scripts/build_pptx.py \
    $TPL/template.pptx .icode_output/ppt/{场景关键词}_edits.json .icode_output/ppt/{名称}.pptx \
    --detail $TPL/detail.json

# 渲染预览（可选）：每页一张 PNG 自检
python3 tools/ppt/scripts/render_slides.py .icode_output/ppt/{名称}.pptx .icode_output/ppt/preview --dpi 144
```

完整编辑规则（只改文字不破坏排版 / 占位必替换 / 禁省略号截断 / 同级字号一致 / 章节呼应）见 [steps/ppt.md](../steps/ppt.md)「内容铁律」。

## 用自备模板（模式B，商用安全）

用户给自家 `.pptx` → 用 `render_slides.py` 渲染 PNG + python-pptx 探查 shape 结构（每页角色/槽位）→
`compute_capacity.py` 算容量 → 生成该模板的 `detail.json` 后走同一套 edits.json 流程。产出到新文件，**不改用户模板原文件**。
