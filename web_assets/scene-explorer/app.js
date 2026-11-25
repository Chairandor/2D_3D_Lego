"use strict";
// Adapted from the A800 OpenMask3D web_demo; query and selection use the original API.
const apiPath = path => "/scene-explorer" + path;
const $ = (id) => document.getElementById(id);
const state = {scene: null, sceneId: null, catalog: [], loadVersion: 0, matches: [], result: null, selected: null, busy: false, selectionVersion: 0, previews: new Map(), ready: false, focused: false};
const colors = ["#397a58", "#94640b", "#b24d3f", "#345995", "#734c98"];
const initialCamera = {up: {x:0,y:0,z:1}, eye: {x:1.5,y:-1.65,z:1.2}, center: {x:0,y:0,z:-.06}};
const config = {responsive:true, displayModeBar:false, scrollZoom:true, displaylogo:false};
const layout = {margin:{l:0,r:0,t:0,b:0}, paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)", showlegend:false, uirevision:"scene", scene:{aspectmode:"data", bgcolor:"rgba(0,0,0,0)", dragmode:"orbit", camera:initialCamera, xaxis:{visible:false}, yaxis:{visible:false}, zaxis:{visible:false}}};

async function api(path, options) {
  const response = await fetch(apiPath(path), options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function coordinates(positions) {
  const x=[],y=[],z=[];
  for(const point of positions){x.push(point[0]);y.push(point[1]);z.push(point[2]);}
  return {x,y,z};
}
function baseTrace(){
  return {type:"scatter3d",mode:"markers",...coordinates(state.scene.positions),hoverinfo:"skip",name:"场景",visible:$("show-context").checked,marker:{size:1.5,opacity:state.selected===null?.85:.22,color:state.scene.colors.map(c=>`rgb(${c[0]},${c[1]},${c[2]})`)}};
}
async function renderScene(){
  if(!state.scene) return;
  const traces=[baseTrace()];
  if(state.selected!==null && state.previews.has(state.selected)){
    const index=state.matches.findIndex(m=>m.instance_id===state.selected);
    traces.push({type:"scatter3d",mode:"markers",...coordinates(state.previews.get(state.selected).positions),name:`实例 ${state.selected}`,hoverinfo:"skip",marker:{size:2.5,opacity:1,color:colors[Math.max(index,0)%colors.length]}});
  }
  await Plotly.react("viewer",traces,{...layout,uirevision:state.sceneId},config);
}
function showImages(urls, labels){
  $("image-grid").replaceChildren();
  if(!urls.length){
    const empty=document.createElement("div");empty.className="crop-placeholder";empty.textContent="这个实例没有保存裁剪图。";$("image-grid").append(empty);return;
  }
  urls.forEach((url,index)=>{
    const figure=document.createElement("figure"), image=document.createElement("img"), caption=document.createElement("figcaption");
    image.src=apiPath(url);image.alt=labels[index];image.loading="lazy";caption.textContent=labels[index];
    figure.append(image,caption);$("image-grid").append(figure);
  });
}
async function selectInstance(match){
  const version=++state.selectionVersion;
  const wasFocused=state.focused;state.focused=false;
  state.selected=match.instance_id;
  document.querySelectorAll(".result-card").forEach(el=>el.classList.toggle("active",Number(el.dataset.id)===match.instance_id));
  $("view-label").textContent=`实例 ${match.instance_id} · 排名 ${match.rank}`;
  $("detail-title").textContent=`实例 ${match.instance_id} · ${match.num_points.toLocaleString()} 个三维点`;
  $("download-instance").href=apiPath(match.download_url);$("download-instance").hidden=false;
  $("use-instance").hidden=window.parent === window;
  showImages(match.crops,match.crops.map((_,i)=>`特征裁剪 · 视角 ${String(i+1).padStart(2,"0")}`));
  $("detail-note").textContent="裁剪图用于提取 CLIP 特征，裁剪中有目标不代表三维掩码正确。请结合三维高亮判断；PLY 包含该候选的全部点与颜色。";
  $("focus").disabled=true;
  try{
    const preview=state.previews.get(match.instance_id)||await api(match.preview_url);
    if(version!==state.selectionVersion) return;
    state.previews.set(match.instance_id,preview);
    await renderScene();
    if(wasFocused) await Plotly.relayout("viewer",{"scene.xaxis.autorange":true,"scene.yaxis.autorange":true,"scene.zaxis.autorange":true,"scene.camera":initialCamera});
    $("focus").disabled=false;
  }catch(error){if(version===state.selectionVersion) setStatus(error.message,true);}
}
function renderResults(){
  $("results").replaceChildren();
  state.matches.forEach(match=>{
    const card=document.createElement("button");card.type="button";card.className="result-card";card.dataset.id=match.instance_id;
    card.setAttribute("aria-label",`查看排名 ${match.rank} 的实例 ${match.instance_id}，相似度 ${match.similarity.toFixed(3)}`);
    const top=document.createElement("div");top.className="card-top";
    const rank=document.createElement("span");rank.className="rank-box";rank.textContent=String(match.rank).padStart(2,"0");
    const id=document.createElement("span");id.className="instance-id";id.textContent=`实例 ${match.instance_id}`;
    const score=document.createElement("span");score.className="similarity";score.textContent=match.similarity.toFixed(3);
    top.append(rank,id,score);
    const bar=document.createElement("div");bar.className="score-bar";const inner=document.createElement("i");inner.style.width=`${Math.max(0,Math.min(100,match.similarity*100))}%`;bar.append(inner);
    const bottom=document.createElement("div");bottom.className="card-bottom";
    const points=document.createElement("span");points.textContent=`${match.num_points.toLocaleString()} 个点`;
    const action=document.createElement("span");action.className="card-arrow";action.textContent="查看实例 ↗";
    bottom.append(points,action);card.append(top,bar,bottom);card.addEventListener("click",()=>selectInstance(match));$("results").append(card);
  });
  $("result-count").textContent=state.matches.length;$("save-json").hidden=false;
}
function setStatus(text,error=false){$("query-status").textContent=text;$("query-status").classList.toggle("error",error);}
function setBusy(busy){
  state.busy=busy;$("search").disabled=busy||!state.ready;$("search").classList.toggle("busy",busy);
  $("scene-select").disabled=busy;
  $("search-label").textContent=busy?"正在匹配三维实例…":"查找物体";
  document.querySelectorAll("[data-prompt]").forEach(el=>el.disabled=busy||!state.ready);
}
async function search(event){
  if(event) event.preventDefault();
  if(state.busy||!state.ready) return;
  const query=$("query").value.trim();if(!query){$("query").focus();return;}
  setBusy(true);setStatus("服务器正在编码文本并计算相似度…");$("query-warning").hidden=true;
  $("use-instance").disabled=true;
  try{
    const result=await api("/api/query",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,top_k:Number($("top-k").value),scene_id:state.sceneId})});
    state.result=result;state.matches=result.matches;renderResults();
    const timing=result.elapsed_ms<1000?`${result.elapsed_ms.toFixed(0)} ms`:`${(result.elapsed_ms/1000).toFixed(2)} s`;
    setStatus(`${result.translated?`${result.query} → ${result.encoded_query} · `:""}${timing}${result.cached?" · 已缓存":""} · 找到 ${result.matches.length} 个候选`);
    $("query-warning").hidden=!result.warning;$("query-warning").textContent=result.warning||"";
    if(state.matches.length) await selectInstance(state.matches[0]);
    else {
      state.selected=null;$("download-instance").hidden=true;$("use-instance").hidden=true;$("focus").disabled=true;
      $("view-label").textContent="完整场景";
      await renderScene();
    }
  }catch(error){setStatus(error.message,true);}
  finally{setBusy(false);$("use-instance").disabled=false;}
}
async function focusInstance(){
  const preview=state.previews.get(state.selected);if(!preview||!preview.positions.length)return;
  state.focused=true;
  const xyz=coordinates(preview.positions),update={"scene.camera":initialCamera};
  for(const axis of ["x","y","z"]){const min=Math.min(...xyz[axis]),max=Math.max(...xyz[axis]),pad=Math.max((max-min)*.15,.12);update[`scene.${axis}axis.range`]=[min-pad,max+pad];update[`scene.${axis}axis.autorange`]=false;}
  $("show-context").checked=false;await renderScene();await Plotly.relayout("viewer",update);
}
async function resetCamera(){
  state.focused=false;
  $("show-context").checked=true;await renderScene();
  await Plotly.relayout("viewer",{"scene.camera":initialCamera,"scene.xaxis.autorange":true,"scene.yaxis.autorange":true,"scene.zaxis.autorange":true});
}
$("query-form").addEventListener("submit",search);
$("prompt-chips").addEventListener("click",event=>{const button=event.target.closest("[data-prompt]");if(button){$("query").value=button.dataset.prompt;search();}});
$("scene-select").addEventListener("change",()=>loadScene($("scene-select").value));
$("show-context").addEventListener("change",renderScene);$("focus").addEventListener("click",focusInstance);$("reset-camera").addEventListener("click",resetCamera);
$("save-json").addEventListener("click",()=>{if(!state.result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.result,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=`${state.sceneId}-query.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
new ResizeObserver(()=>{if($("viewer")._fullLayout) Plotly.Plots.resize($("viewer"));}).observe($("viewer"));
async function loadScene(sceneId){
  const version=++state.loadVersion;
  ++state.selectionVersion;
  state.ready=false;state.scene=null;state.sceneId=sceneId;state.selected=null;state.matches=[];state.result=null;state.previews.clear();state.focused=false;
  setBusy(true);setStatus("正在加载所选场景…");
  $("search-label").textContent="正在加载场景…";
  $("results").replaceChildren();$("result-count").textContent="—";$("save-json").hidden=true;$("download-instance").hidden=true;$("use-instance").hidden=true;$("query-warning").hidden=true;$("focus").disabled=true;
  $("show-context").checked=true;$("view-label").textContent="完整场景";$("scene-select").value=sceneId;
  $("show-context").disabled=true;$("reset-camera").disabled=true;
  $("viewer-loading").hidden=false;$("viewer-loading").querySelector("p").textContent="正在加载三维场景";$("viewer-loading").querySelector("small").textContent="正在传输点云预览";$("viewer-loading").querySelector(".loader").hidden=false;
  try{
    const metadata=state.catalog.find(scene=>scene.scene_id===sceneId);
    if(!metadata) throw new Error("场景不存在。");
    $("scene-title").textContent=metadata.title;$("scene-description").textContent=metadata.description;
    $("point-count").textContent=metadata.num_points.toLocaleString();$("mask-count").textContent=metadata.num_masks;$("valid-count").textContent=metadata.searchable_instances;
    $("connection").className="connection ready";
    $("connection").querySelector("span").textContent=metadata.device?.startsWith("cuda:")
      ? `GPU ${metadata.device.slice(5)} · 服务就绪` : "服务就绪";
    $("preview-count").textContent=`${metadata.preview_points.toLocaleString()} 点预览`;
    $("prompt-chips").replaceChildren();
    metadata.prompts.forEach(prompt=>{const button=document.createElement("button");button.type="button";button.dataset.prompt=prompt.query;button.textContent=prompt.label;button.disabled=true;$("prompt-chips").append(button);});
    $("query").value=(metadata.prompts[0]||{}).query||"a chair";
    $("query").placeholder=$("query").value;
    $("detail-title").textContent="场景原始视角";
    $("detail-note").textContent="查询后，点击候选查看三维高亮与特征裁剪。相似度排名不能保证目标存在或分割正确。";
    showImages(metadata.frames.map(frame=>`/api/frames/${frame}.jpg?scene=${encodeURIComponent(sceneId)}`),metadata.frames.map((_,i)=>`场景视角 ${String(i+1).padStart(2,"0")}`));
    const scene=await api(`/api/scene?scene=${encodeURIComponent(sceneId)}`);
    if(version!==state.loadVersion) return;
    state.scene=scene;Plotly.purge("viewer");await renderScene();await resetCamera();
    $("viewer-loading").hidden=true;state.ready=true;
    const url=new URL(window.location.href);url.searchParams.set("scene",sceneId);history.replaceState(null,"",url);
    setStatus("场景已就绪。点击示例提示词，或输入自己的描述。");
  }catch(error){
    $("connection").className="connection error";$("connection").querySelector("span").textContent="连接失败";
    $("viewer-loading").querySelector("p").textContent="场景加载失败";$("viewer-loading").querySelector("small").textContent=error.message;$("viewer-loading").querySelector(".loader").hidden=true;
    setStatus(error.message,true);
  }
  finally{if(version===state.loadVersion){setBusy(false);$("show-context").disabled=!state.ready;$("reset-camera").disabled=!state.ready;}}
}
async function initialize(){
  $("search").disabled=true;
  try{
    const catalog=await api("/api/scenes");state.catalog=catalog.scenes;
    $("scene-select").replaceChildren();
    state.catalog.forEach(scene=>{const option=document.createElement("option");option.value=scene.scene_id;option.textContent=scene.title;$("scene-select").append(option);});
    const requested=new URLSearchParams(location.search).get("scene");
    await loadScene(state.catalog.some(scene=>scene.scene_id===requested)?requested:catalog.default_scene);
  }catch(error){$("connection").className="connection error";$("connection").querySelector("span").textContent="连接失败";$("viewer-loading").querySelector("p").textContent="场景列表加载失败";$("viewer-loading").querySelector("small").textContent=error.message;$("viewer-loading").querySelector(".loader").hidden=true;setStatus(error.message,true);}
}
$("use-instance").addEventListener("click", () => {
  if (state.selected === null || !state.sceneId || window.parent === window) return;
  window.parent.postMessage({type: "openmask-instance", scene: state.sceneId, instance: state.selected}, location.origin);
});
if (window.parent !== window) {
  new ResizeObserver(() => {
    window.parent.postMessage({type: "openmask-height", height: document.body.scrollHeight}, location.origin);
  }).observe(document.querySelector(".workspace"));
}
initialize();
