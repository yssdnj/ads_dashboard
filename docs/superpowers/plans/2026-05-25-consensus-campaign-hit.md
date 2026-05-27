# Consensus 入围广告活动命中明细 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在三轮和六轮 consensus 标签页末尾新增「入围广告活动命中明细」区块，按广告类别 → 广告组合 → 广告活动 → Targeting 四层折叠树展示命中详情。

**Architecture:** 纯前端改动，在 `renderTargetingResult` 函数末尾插入第 10 节渲染逻辑。通过 `roundKey === 'consensus'` 条件判断只在 consensus 面板渲染。数据来自已有的 consensus rows（含 `category`、`portfolio`、`campaign`、`hit_rounds` 等字段），无需后端改动。

**Tech Stack:** 原生 JavaScript、HTML `<details>/<summary>`、内联 CSS（与现有第 9 节「层级明细」风格一致）

---

### Task 1: 在 renderTargetingResult 插入「入围广告活动命中明细」区块

**Files:**
- Modify: `ads_funnel/template.html`（在现有第 9 节闭合前插入第 10 节，约第 3822 行）

**背景（必读）：**

`renderTargetingResult(data, targetId, skipSideEffects)` 在文件约 3569 行定义，最后两行是：
```javascript
  html+='</div></div>';   // 第 3822 行：关闭第9节容器 + 关闭 tgt-wrap
  el.innerHTML = html;    // 第 3823 行
```

新区块插入位置：`html+='</div></div>'` **之前**（即在最终关闭 tgt-wrap 之前）。

函数内已定义的辅助变量（可直接使用）：
- `roundKey` — 当前面板标识，`'consensus'` 时才渲染新区块
- `rows` — 当前数据集的所有 targeting rows
- `trn(s, n)` — 截断字符串到 n 字符并加省略号
- `tag(txt, cls)` — 生成 `<span class="tgt-tag cls">txt</span>`
- `labelCls` — label → CSS class 映射对象（已在第 3775 行定义）

- [ ] **Step 1: 在 `html+='</div></div>'` 行之前插入第 10 节代码**

找到文件中的这段（约第 3821–3823 行）：
```javascript
    html+='</details>';
  });
  html+='</div></div>';
  el.innerHTML = html;
}
```

将其替换为：
```javascript
    html+='</details>';
  });
  html+='</div>';

  // ── 10. 入围广告活动命中明细（仅 consensus 面板）────────────────────────────
  if(roundKey==='consensus'){
    // 解析命中轮次字符串 → 数组，如 "R1,R2,R4" → ['R1','R2','R4']
    const _parseRounds = hrStr=>(hrStr||'').split(',').map(r=>r.trim()).filter(Boolean);

    // 轮次徽章样式
    const _roundStyle = r=>({
      'R1':'background:#ede9fe;color:#7c3aed',
      'R2':'background:#e0f2fe;color:#0891b2',
      'R3':'background:#ccfbf1;color:#0f766e',
      'R4':'background:#dcfce7;color:#16a34a',
      'R5':'background:#fef9c3;color:#ca8a04',
      'R6':'background:#ffedd5;color:#ea580c',
    }[r]||'background:#f1f5f9;color:#64748b');

    // 规则推断说明（优先级从高到低）
    const _hitDesc = hrStr=>{
      const nums=_parseRounds(hrStr).map(r=>parseInt(r.replace('R',''))).sort((a,b)=>a-b);
      const hasR1=nums.includes(1);
      const n=nums.length;
      if(n>=5) return '长期持续异常，优先处理';
      if(hasR1){
        const isConsec=nums.every((v,i)=>i===0||v===nums[i-1]+1);
        if(isConsec&&n>=3) return '近期持续异常，建议尽快处理';
        if(n>=3) return '近期复发，历史有前例';
        return '近期出现，观察是否持续';
      }
      return '早期问题，近期已有改善迹象';
    };

    // 聚合：category → portfolio → campaign → rows[]
    const hitTree={};
    rows.forEach(r=>{
      const cat=r.category||'7_Other';
      const port=r.portfolio||'(无组合)';
      const camp=r.campaign||'(未知广告活动)';
      if(!hitTree[cat]) hitTree[cat]={};
      if(!hitTree[cat][port]) hitTree[cat][port]={};
      if(!hitTree[cat][port][camp]) hitTree[cat][port][camp]=[];
      hitTree[cat][port][camp].push(r);
    });

    html+=`<div class="tgt-sec-div">入围广告活动命中明细 <span style="font-weight:400;color:var(--g4)">· 默认折叠，点击展开</span></div>`;

    if(rows.length===0){
      html+=`<div class="tgt-card" style="color:var(--g4);text-align:center;padding:20px">暂无入围广告活动</div>`;
    } else {
      html+=`<div style="background:#fff;border:1px solid var(--g2);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh)">`;

      Object.entries(hitTree).sort((a,b)=>a[0].localeCompare(b[0])).forEach(([cat,ports])=>{
        const catTotal=Object.values(ports).reduce((s,camps)=>s+Object.values(camps).reduce((s2,rs)=>s2+rs.length,0),0);
        html+=`<details style="border-top:1px solid var(--g1)">
          <summary style="padding:11px 16px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;background:var(--g0);user-select:none">
            <span style="font-size:12px;font-weight:700;color:var(--g9);flex:1">▶ ${cat}</span>
            <span style="font-size:11px;color:var(--g6)">${catTotal} 个 targeting</span>
          </summary>`;

        Object.entries(ports).sort((a,b)=>a[0].localeCompare(b[0])).forEach(([port,camps])=>{
          const portTotal=Object.values(camps).reduce((s,rs)=>s+rs.length,0);
          html+=`<details style="border-top:1px solid var(--g1)">
            <summary style="padding:9px 16px 9px 30px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;background:#fff;user-select:none">
              <span style="font-size:11px;font-weight:600;color:var(--g7);flex:1">📁 ${port}</span>
              <span style="font-size:11px;color:var(--g6)">${portTotal} 个 targeting</span>
            </summary>`;

          Object.entries(camps).sort((a,b)=>a[0].localeCompare(b[0])).forEach(([camp,campRows])=>{
            html+=`<details style="border-top:1px solid var(--g1)">
              <summary style="padding:8px 16px 8px 48px;cursor:pointer;list-style:none;display:flex;align-items:center;gap:10px;background:var(--g0);user-select:none">
                <span style="font-size:11px;color:var(--g6);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${camp}">📣 ${trn(camp,56)}</span>
                <span style="font-size:11px;color:var(--g6);flex-shrink:0">${campRows.length} 个 targeting</span>
              </summary>
              <div style="padding-left:64px;overflow-x:auto;background:#fff">
                <table class="tgt-tbl" style="min-width:700px">
                  <tr><th>Targeting</th><th>Match</th><th>标签</th><th style="text-align:right">调价</th><th>命中轮次</th><th>说明</th></tr>
                  ${campRows.map(r=>{
                    const rounds=_parseRounds(r.hit_rounds||'');
                    const badges=rounds.map(rn=>`<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:700;margin-right:3px;${_roundStyle(rn)}">${rn}</span>`).join('');
                    const desc=_hitDesc(r.hit_rounds||'');
                    return `<tr>
                      <td style="font-family:monospace;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.targeting}">${trn(r.targeting,32)}</td>
                      <td>${tag(r.match_type,'gr')}</td>
                      <td style="white-space:nowrap">${tag(r.label,labelCls[r.label]||'gr')}</td>
                      <td style="text-align:right;font-family:monospace;font-weight:700;color:var(--rd)">${r.adj_pct}</td>
                      <td style="white-space:nowrap">${badges}</td>
                      <td style="color:var(--g6);font-size:12px;white-space:nowrap">${desc}</td>
                    </tr>`;
                  }).join('')}
                </table>
              </div>
            </details>`;
          });
          html+='</details>';
        });
        html+='</details>';
      });
      html+='</div>';
    }
  }

  html+='</div>';  // 关闭 tgt-wrap
  el.innerHTML = html;
}
```

- [ ] **Step 2: 手动验证**

启动服务（如未运行）：
```bash
cd C:\Users\admin\Desktop\python\ads_dashboard\ads_funnel
python start.py
```

在浏览器中打开报告页，进入「批量竞价优化」或「批量竞价优化-6轮」Tab：
1. 运行一次分析（填入产品标识、目标ACoS、平均点击数）
2. 等待分析完成后，点击「Consensus」子标签
3. 向下滚动，确认出现「入围广告活动命中明细」区块
4. 展开各层级，确认：
   - 广告类别层：显示「N 个 targeting」数量
   - 广告组合层：显示「N 个 targeting」数量
   - 广告活动层：显示「N 个 targeting」数量
   - Targeting 行：6 列均正确（Targeting / Match / 标签 / 调价 / 命中轮次徽章 / 说明）
5. 点击 R1、R2 等非 consensus 面板，确认**不显示**「入围广告活动命中明细」区块
6. 如 consensus rows 为空，确认显示「暂无入围广告活动」占位文字

- [ ] **Step 3: Commit**

```bash
cd C:\Users\admin\Desktop\python\ads_dashboard
git add ads_funnel/template.html
git commit -m "feat: add consensus campaign hit detail tree (category→portfolio→campaign→targeting)"
```
