import{o as T,a as b,c as B,b as t,w as a,Y as E,E as u,g as r,f as c,D as N,B as P,d as U,i as o,k as y,t as g,U as D,Z as I}from"./index-p8TSCqI8.js";import{_ as M}from"./_plugin-vue_export-helper-DlAUqK2U.js";const O={class:"callback-settings"},j=`{
  "event": "task_completed",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:00+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total": 3,
    "completed_at": "2026-05-12T15:30:00",
    "results": [
      {
        "id": 1,
        "url": "https://www.douyin.com/video/xxx",
        "platform": "dy",
        "platform_name": "抖音",
        "is_valid": true,
        "praise_count": 100,
        "reply_count": 50,
        "visit_count": 1000,
        "share_count": 20,
        "author": "xxx",
        "title": "xxx"
      }
    ]
  }
}`,A=`{
  "event": "comments_ready",
  "task_id": "batch-abc123",
  "status": "completed",
  "timestamp": "2026-05-12T15:30:05+00:00",
  "data": {
    "task_id": "batch-abc123",
    "total_comments": 30,
    "results": [
      {
        "content_url": "https://www.douyin.com/video/xxx",
        "platform": "dy",
        "comments": [
          {
            "comment_id": "xxx",
            "author_name": "用户A",
            "comment_text": "好看!",
            "comment_like_count": 5,
            "comment_time": "2026-05-10 12:00:00"
          }
        ]
      }
    ]
  }
}`,Y={__name:"CallbackSettings",setup(Z){const l=c({enabled:!1,url:"",max_retries:3,retry_intervals:[5,15,30]}),m=c(!1),d=c(!1),p=D({get:()=>(l.value.retry_intervals||[]).join(","),set:s=>{l.value.retry_intervals=s.split(",").map(e=>parseInt(e.trim())).filter(e=>!isNaN(e))}});async function _(){m.value=!0;try{const s=await E();l.value={...s}}catch(s){u.error("加载配置失败: "+s.message)}finally{m.value=!1}}async function k(){d.value=!0;try{const s=await I(l.value);l.value={...s},u.success("配置已保存")}catch(s){u.error("保存失败: "+s.message)}finally{d.value=!1}}return T(_),(s,e)=>{const w=r("el-switch"),i=r("el-form-item"),v=r("el-input"),V=r("el-input-number"),f=r("el-button"),C=r("el-form"),x=r("el-card"),S=N("loading");return b(),B("div",O,[t(x,{shadow:"never"},{header:a(()=>[...e[4]||(e[4]=[o("span",{class:"section-title"},"全局回调配置",-1)])]),default:a(()=>[P((b(),U(C,{model:l.value,"label-width":"140px",class:"config-form"},{default:a(()=>[t(i,{label:"启用全局回调"},{default:a(()=>[t(w,{modelValue:l.value.enabled,"onUpdate:modelValue":e[0]||(e[0]=n=>l.value.enabled=n)},null,8,["modelValue"]),e[5]||(e[5]=o("span",{class:"tip-text"},"开启后，所有任务完成时会自动 POST 结果到回调地址",-1))]),_:1}),t(i,{label:"全局回调地址"},{default:a(()=>[t(v,{modelValue:l.value.url,"onUpdate:modelValue":e[1]||(e[1]=n=>l.value.url=n),placeholder:"如 https://your-agent-hub.com/api/callback",clearable:""},null,8,["modelValue"]),e[6]||(e[6]=o("div",{class:"tip-text"},"各任务提交时可传入 callback_url 覆盖此全局地址",-1))]),_:1}),t(i,{label:"最大重试次数"},{default:a(()=>[t(V,{modelValue:l.value.max_retries,"onUpdate:modelValue":e[2]||(e[2]=n=>l.value.max_retries=n),min:0,max:10},null,8,["modelValue"])]),_:1}),t(i,{label:"重试间隔(秒)"},{default:a(()=>[t(v,{modelValue:p.value,"onUpdate:modelValue":e[3]||(e[3]=n=>p.value=n),placeholder:"如 5,15,30"},null,8,["modelValue"]),e[7]||(e[7]=o("div",{class:"tip-text"},"逗号分隔的秒数，依次使用",-1))]),_:1}),t(i,null,{default:a(()=>[t(f,{type:"primary",onClick:k,loading:d.value},{default:a(()=>[...e[8]||(e[8]=[y("保存配置",-1)])]),_:1},8,["loading"]),t(f,{onClick:_},{default:a(()=>[...e[9]||(e[9]=[y("重新加载",-1)])]),_:1})]),_:1})]),_:1},8,["model"])),[[S,m.value]])]),_:1}),t(x,{shadow:"never",style:{"margin-top":"16px"}},{header:a(()=>[...e[10]||(e[10]=[o("span",{class:"section-title"},"回调 Payload 规范",-1)])]),default:a(()=>[o("div",{class:"doc-section"},[e[11]||(e[11]=o("h4",null,"主结果回调 (event: task_completed)",-1)),o("pre",{class:"code-block"},g(j)),e[12]||(e[12]=o("h4",{style:{"margin-top":"16px"}},"评论回调 (event: comments_ready)",-1)),o("pre",{class:"code-block"},g(A)),e[13]||(e[13]=o("div",{class:"tip-text",style:{"margin-top":"12px"}}," 说明：如果任务开启了评论抓取，会分两次 POST：先发主结果，再发评论数据。 如果提交任务时传了 callback_url，优先用任务级地址；未传则使用上方的全局地址。 ",-1))])]),_:1})])}}},z=M(Y,[["__scopeId","data-v-e07711a0"]]);export{z as default};
