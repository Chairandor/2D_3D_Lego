'use strict';
const $ = id => document.getElementById(id);
let config, currentOp, activeJob = null, viewPath = '', browsePath = '', browseEntries = [], activePane = 'preview';
let sceneData = null, vertices = [], yaw = -.65, pitch = .42, zoom = 1, previewSerial = 0, playTimer = null;
let lastResultKey = '', selectedHistory = false, lastArtifacts = [];
const canvas = $('canvas'), ctx = canvas.getContext('2d');
const names = {orthographic:'正交填充（默认）',holes:'孔洞填充',surface:'表面壳层',poisson:'Poisson',
  'ball-pivoting':'Ball Pivoting',tsdf:'TSDF Fusion',
  table:'桌面（无实体底板）',baseplate:'虚拟插销底板',fast:'Fast',balanced:'Balanced',direct:'Direct',sf3d:'已知 SF3D 来源'};
const statuses = {running:'运行中',stopping:'正在停止',complete:'执行完成',partial:'部分结果',failed:'执行失败',cancelled:'已停止',pending:'等待'};
const geometryExtensions = ['stl','ply','npz','obj','off','gltf','glb','pcd','xyz','xyzn','xyzrgb','pts','ldr','mpd'];
function el(tag, text, cls) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function showNotice(message, kind='error'){const box=$('notice');box.textContent=message;box.className='notice '+kind;box.hidden=false;}
function clearNotice(){$('notice').hidden=true;}
async function api(path, payload){
  const init=payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Legolizer-Request':'1'},body:JSON.stringify(payload)};
  const response=await fetch(path,init), data=await response.json();
  if(!response.ok)throw new Error(data.error||'请求失败');return data;
}
function busy(){return activeJob && ['running','stopping'].includes(activeJob.status);}
function setPane(pane){activePane=pane;for(const id of ['preview','results']){$(id+'Pane').hidden=id!==pane;$(id+'Tab').classList.toggle('active',id===pane);$(id+'Tab').setAttribute('aria-selected',String(id===pane));}if(pane==='preview')draw();}
function setOperation(id, defaults={}){
  currentOp=config.operations.find(op=>op.id===id);$('operation').value=id;$('opNote').textContent=currentOp.note;
  const form=$('params');form.replaceChildren();const groups=new Map();
  for(const spec of currentOp.fields){
    let grid=groups.get(spec.group);
    if(!grid){if(groups.size)form.append(el('div',spec.group,'group-title'));grid=el('div',undefined,'field-grid');form.append(grid);groups.set(spec.group,grid);}
    const wrap=el('div');const id='param_'+spec.key;let input;
    if(spec.type==='bool'){const label=el('label',undefined,'check');input=el('input');input.type='checkbox';input.checked=defaults[spec.key]??spec.default;label.append(input,document.createTextNode(spec.label));wrap.append(label);wrap.className='wide';}
    else {const label=el('label',spec.label);label.htmlFor=id;wrap.append(label);
      if(spec.choices){input=el('select');for(const value of spec.choices){const option=el('option',names[value]||value);option.value=value;input.append(option);}}
      else {input=el('input');input.type='number';input.step=spec.type==='int'?'1':'any';if(spec.minimum!==null)input.min=spec.minimum;if(spec.maximum!==null)input.max=spec.maximum;}
      input.value=defaults[spec.key]??spec.default;wrap.append(input);
      if(spec.key==='source_unit_mm'||spec.key==='orientation')wrap.className='wide';
    }
    input.id=id;input.name=spec.key;grid.append(wrap);
  }
  if(!currentOp.fields.length)form.append(el('p','使用文件中记录的毫米尺寸与原点，无需额外参数。','hint'));
  if(!busy())renderStages();
}
function options(){const out={};for(const f of currentOp.fields){const input=$('param_'+f.key);out[f.key]=f.type==='bool'?input.checked:f.type==='int'||f.type==='float'?Number(input.value):input.value;}return out;}
function renderStages(stages){
  const items=stages||[{label:'输入检查',state:'pending'},...currentOp.stages.map(label=>({label,state:'pending'}))];
  $('stages').replaceChildren();items.forEach((stage,index)=>{if(index)$('stages').append(el('span','→','stage-arrow'));const node=el('div',undefined,'stage '+stage.state);node.append(el('span',stage.state==='complete'?'✓':String(index+1).padStart(2,'0'),'stage-icon'),el('span',stage.label));$('stages').append(node);});
}
async function start(){
  clearNotice();if(!$('params').reportValidity())return;
  $('runBtn').disabled=true;
  try{await api('/api/start',{operation:currentOp.id,input:$('inputPath').value.trim(),options:options()});selectedHistory=false;lastResultKey='';await refresh();}
  catch(error){showNotice(error.message);$('runBtn').disabled=!!busy();}
}
async function stop(){try{await api('/api/stop',{});await refresh();}catch(error){showNotice(error.message);}}
function summarize(values){
  const box=$('checks');box.replaceChildren();
  if(!values.length){box.append(el('p','检查结果会在后端写出报告后显示。','report-note'));return;}
  const report=values.find(r=>r.kind==='strict');
  if(report){
    const verdicts=el('div',undefined,'verdicts');
    for(const [key,label] of [['buildable','可构建性'],['stable','稳定性'],['instructions_status','说明书状态']]){
      const value=report[key], card=el('div',undefined,'verdict');card.append(el('span',label));
      card.append(el('b',value===true?'通过':value===false?'未通过':value??'未报告',value===true?'good':value===false?'bad':'warn'));verdicts.append(card);
    }
    box.append(verdicts);
    const numbers=[];if(report.brick_count!=null)numbers.push(report.brick_count+' 件');if(report.instruction_step_count!=null)numbers.push(report.instruction_step_count+' 步');
    if(numbers.length)box.append(el('p',numbers.join(' · '),'report-note'));
    box.append(el('p','legolization 的构建、稳定性与说明书是独立判定。partial / 未报告不能视为认证通过；baseplate 模式不等同于无底座桌面稳定。','report-note'));
    const warnings=report.warnings||[];if(warnings.length)box.append(el('p','后端提示：'+warnings.map(w=>typeof w==='string'?w:JSON.stringify(w)).join('；'),'report-note warn'));
  }else box.append(el('p','此处是历史布局报告；本工作台的新 LEGO 任务均使用 legolization。','report-note'));
}
function renderArtifacts(items,reportValues=[]){
  lastArtifacts=items;$('artifactCount').textContent=items.length;$('artifacts').replaceChildren();$('artifacts').className=items.length?'':'empty';
  if(!items.length)$('artifacts').textContent='任务运行后将在这里显示中间模型、BOM 和检查报告。';
  for(const item of items){
    const ext=item.path.split('.').pop().toLowerCase(), row=el('div',undefined,'artifact'),info=el('div',undefined,'artifact-info'),actions=el('div',undefined,'artifact-actions');
    row.append(el('span',ext.toUpperCase(),'file-icon'));info.append(el('b',item.name),el('small',(item.size/1048576).toFixed(2)+' MiB'));row.append(info);
    if(geometryExtensions.includes(ext)){const preview=el('button','预览');preview.addEventListener('click',()=>loadPreview(item.path,'z'));actions.append(preview);}
    if(item.can_input){
      const next=el('button','用作输入');next.addEventListener('click',()=>{setInput(item.path);showNotice('已选择产物作为输入，请选择下一条转换路径。','success');});actions.append(next);
    }
    const link=el('a','下载');link.href=item.url;link.setAttribute('download','');actions.append(link);row.append(actions);$('artifacts').append(row);
  }
  summarize(reportValues);
}
async function refresh(){
  try{
    const data=await api('/api/status');$('connection').textContent='已连接';
    activeJob=data.job;$('runBtn').disabled=!!busy();$('stopBtn').disabled=!busy()||activeJob.status==='stopping';
    if(activeJob){
      $('status').className='badge '+activeJob.status;$('status').textContent=statuses[activeJob.status]||activeJob.status;
      $('runTitle').textContent=config.operations.find(o=>o.id===activeJob.operation)?.label||activeJob.operation;
      $('outputPath').textContent=activeJob.output;$('elapsed').textContent=(data.elapsed??0)+' 秒';
      renderStages(activeJob.stages);$('log').textContent=data.logs||'正在启动转换进程……';$('logState').textContent=data.backend_progress||activeJob.message;
      if($('autoscroll').checked)$('log').scrollTop=$('log').scrollHeight;
      const key=activeJob.id+':'+activeJob.status+':'+data.artifacts.length;
      if(!selectedHistory&&key!==lastResultKey){renderArtifacts(data.artifacts,data.reports);lastResultKey=key;}
    }
  }catch(error){$('connection').textContent='连接中断';if(activeJob&&busy())$('logState').textContent='连接中断；重连后读取任务状态';}
}
function setInput(path){$('inputPath').value=path;}
async function browse(path=''){
  try{const data=await api('/api/browse?path='+encodeURIComponent(path||'.'));browsePath=data.path;browseEntries=data.entries;$('browsePath').textContent='/'+data.path;$('fileSearch').value='';renderFileList();if(!$('browser').open)$('browser').showModal();}
  catch(error){showNotice(error.message);}
}
function renderFileList(){
  const list=$('fileList'),query=$('fileSearch').value.toLowerCase();list.replaceChildren();
  for(const item of browseEntries.filter(i=>i.name.toLowerCase().includes(query))){const row=el('button',undefined,'file-row');row.append(el('span',item.directory?'▸':'·','folder-icon'),el('span',item.name),el('small',item.directory?'目录':(item.size/1048576).toFixed(2)+' MiB'));
    row.addEventListener('click',()=>{if(item.directory)browse(item.path);else {setInput(item.path);$('browser').close();}});list.append(row);
  }
  if(!list.children.length)list.append(el('div','当前目录中没有匹配的模型或配置文件。','empty'));
}
async function upload(file){
  if(!file)return;if(file.size>128*1048576){showNotice('单文件上传上限 128 MiB；请把大文件放入项目文件夹后浏览选择。');return;}
  $('uploadBtn').disabled=true;
  try{const response=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',headers:{'X-Legolizer-Request':'1'},body:file});const data=await response.json();if(!response.ok)throw new Error(data.error);setInput(data.path);showNotice('文件已导入，可以调整参数后开始转换。','success');}
  catch(error){showNotice(error.message);}finally{$('uploadBtn').disabled=false;$('upload').value='';}
}
async function history(){
  try{const jobs=await api('/api/history');const list=$('historyList');list.replaceChildren();for(const job of jobs){const row=el('button',undefined,'file-row');const desc=el('span');desc.append(el('b',config.operations.find(o=>o.id===job.operation)?.label||job.operation),el('div',job.id,'hint'));row.append(desc,el('small',statuses[job.status]||job.status));row.addEventListener('click',async()=>{try{const data=await api('/api/result?path='+encodeURIComponent(job.output));selectedHistory=true;renderArtifacts(data.artifacts,data.reports);$('history').close();setPane('results');showNotice('正在查看历史运行 '+job.id+'。','success');}catch(error){showNotice(error.message);}});list.append(row);}if(!jobs.length)list.append(el('div','还没有工作台运行记录；已有模型可以从项目文件中选择预览。','empty'));$('history').showModal();}
  catch(error){showNotice(error.message);}
}
function stopPlayback(){if(playTimer){clearInterval(playTimer);playTimer=null;}$('playSteps').textContent='播放';}
function showStep(){if(!sceneData)return;$('stepValue').textContent=$('stepSlider').value+' / '+sceneData.max_step;draw();}
async function loadPreview(path,axis){
  if(!path){showNotice('请先选择输入文件。');return;}
  const serial=++previewSerial;stopPlayback();setPane('preview');$('viewerMessage').replaceChildren(el('b','正在读取几何……'),el('span','较大的网格会先简化用于交互预览'));$('viewerMessage').hidden=false;
  $('previewName').textContent=path;
  try{
    const data=await api('/api/preview?path='+encodeURIComponent(path)+'&axis='+encodeURIComponent(axis||$('previewAxis').value));if(serial!==previewSerial)return;
    sceneData=data;viewPath=path;$('previewAxis').value=axis||$('previewAxis').value;
    const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const point of data.vertices)for(let i=0;i<3;i++){lo[i]=Math.min(lo[i],point[i]);hi[i]=Math.max(hi[i],point[i]);}
    const size=Math.max(...hi.map((v,i)=>v-lo[i]))||1,center=lo.map((v,i)=>(v+hi[i])/2);vertices=data.vertices.map(p=>p.map((v,i)=>(v-center[i])/size));
    yaw=-.65;pitch=.42;zoom=1;$('viewerMessage').hidden=true;$('previewStats').replaceChildren();
    for(const [label,value] of Object.entries(data.stats||{})){const node=el('div',undefined,'metric');node.append(el('div',Array.isArray(value)?value.join(' × '):String(value),'value'),el('div',label,'label'));$('previewStats').append(node);}
    $('previewNote').textContent=data.note;$('stepControl').hidden=data.kind!=='bricks';$('stepSlider').max=data.max_step;$('stepSlider').value=data.max_step;showStep();draw();
  }catch(error){if(serial!==previewSerial)return;sceneData=null;vertices=[];$('viewerMessage').replaceChildren(el('b','无法预览此文件'),el('span',error.message));$('viewerMessage').hidden=false;$('previewStats').replaceChildren();$('stepControl').hidden=true;$('previewNote').textContent='可通过产物下载链接，在对应工具中查看完整文件。';draw();}
}
function rotate(point){const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch),x=point[0]*cy-point[1]*sy,y=point[0]*sy+point[1]*cy;return [x,y*sp+point[2]*cp,y*cp-point[2]*sp];}
function draw(){
  if(activePane!=='preview')return;const rect=canvas.getBoundingClientRect();if(!rect.width||!rect.height)return;const dpr=Math.min(devicePixelRatio||1,2);if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);}ctx.setTransform(dpr,0,0,dpr,0,0);
  const width=rect.width,height=rect.height;ctx.clearRect(0,0,width,height);const scale=Math.min(width,height)*.74*zoom;
  const project=p=>{const r=rotate(p);return [width/2+r[0]*scale,height/2-r[1]*scale,r[2]];};
  ctx.lineWidth=1;ctx.strokeStyle='#1c2c3e';for(let i=-5;i<=5;i++){for(const pair of [[[i/6,-.85,-.55],[i/6,.85,-.55]],[[-.85,i/6,-.55],[.85,i/6,-.55]]]){const a=project(pair[0]),b=project(pair[1]);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();}}
  if(!sceneData)return;const projected=vertices.map(project);
  if(sceneData.kind==='pointcloud'){
    const order=projected.map((p,i)=>[p[2],i]).sort((a,b)=>b[0]-a[0]);for(const [,i] of order){const p=projected[i],color=sceneData.colors[i]||[112,208,195];ctx.fillStyle='rgb('+color.join(',')+')';const s=Math.max(1,1.6*zoom);ctx.fillRect(p[0],p[1],s,s);}return;
  }
  const step=Number($('stepSlider').value), faces=[];
  for(let i=0;i<sceneData.faces.length;i++){if((sceneData.steps[i]||1)>step)continue;const indices=sceneData.faces[i],pts=indices.map(j=>projected[j]);faces.push({i,indices,pts,depth:pts.reduce((v,p)=>v+p[2],0)/pts.length});}
  faces.sort((a,b)=>b.depth-a.depth);
  for(const face of faces){
    const [a,b,c]=face.indices.map(i=>vertices[i]);const u=b.map((v,i)=>v-a[i]),v=c.map((n,i)=>n-a[i]);const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],length=Math.hypot(...n)||1;
    const brightness=.52+.48*Math.abs((n[0]*.3+n[1]*-.5+n[2]*.81)/length);
    const base=sceneData.colors[face.i]||[91,181,172];ctx.fillStyle='rgb('+base.map(n=>Math.round(Math.min(255,n*brightness))).join(',')+')';ctx.beginPath();face.pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fill();
    if(sceneData.kind==='bricks'||sceneData.kind==='voxel'){ctx.strokeStyle='#09121d99';ctx.lineWidth=.55;ctx.stroke();}
  }
}
let drag=null;
canvas.addEventListener('pointerdown',event=>{drag={x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener('pointermove',event=>{if(!drag)return;yaw+=(event.clientX-drag.x)*.009;pitch=Math.max(-1.4,Math.min(1.4,pitch+(event.clientY-drag.y)*.009));drag={x:event.clientX,y:event.clientY};draw();});
canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
canvas.addEventListener('wheel',event=>{event.preventDefault();zoom=Math.max(.25,Math.min(5,zoom*Math.exp(-event.deltaY*.001)));draw();},{passive:false});
new ResizeObserver(draw).observe(canvas);
$('resetView').addEventListener('click',()=>{yaw=-.65;pitch=.42;zoom=1;draw();});
$('stepSlider').addEventListener('input',()=>{stopPlayback();showStep();});
$('playSteps').addEventListener('click',()=>{if(playTimer){stopPlayback();return;}if(Number($('stepSlider').value)>=sceneData.max_step)$('stepSlider').value=1;$('playSteps').textContent='暂停';showStep();playTimer=setInterval(()=>{const next=Number($('stepSlider').value)+1;if(next>sceneData.max_step){stopPlayback();return;}$('stepSlider').value=next;showStep();},600);});
$('previewAxis').addEventListener('change',()=>{if(viewPath)loadPreview(viewPath,$('previewAxis').value);});
$('previewTab').addEventListener('click',()=>setPane('preview'));$('resultsTab').addEventListener('click',()=>setPane('results'));
$('runBtn').addEventListener('click',start);$('stopBtn').addEventListener('click',stop);
$('params').addEventListener('submit',event=>event.preventDefault());
$('operation').addEventListener('change',()=>setOperation($('operation').value));$('resetOptions').addEventListener('click',()=>setOperation(currentOp.id));
$('browseBtn').addEventListener('click',()=>browse(browsePath));$('closeBrowser').addEventListener('click',()=>$('browser').close());
$('parentDir').addEventListener('click',()=>browse(browsePath.split('/').slice(0,-1).join('/')));$('fileSearch').addEventListener('input',renderFileList);
$('uploadBtn').addEventListener('click',()=>$('upload').click());$('upload').addEventListener('change',()=>upload($('upload').files[0]));
$('previewInput').addEventListener('click',()=>{const axis=$('param_orientation')?.value;loadPreview($('inputPath').value.trim(),['x','y','z','-x','-y','-z'].includes(axis)?axis:$('previewAxis').value);});
$('historyBtn').addEventListener('click',history);$('closeHistory').addEventListener('click',()=>$('history').close());
document.querySelectorAll('[data-source]').forEach(button=>button.addEventListener('click',()=>{const path=button.dataset.source;setInput(path);if($('param_orientation'))$('param_orientation').value=path.endsWith('/knot.stl')?'-x':'y';if($('param_source_unit_mm'))$('param_source_unit_mm').value=1000;loadPreview(path,path.endsWith('/knot.stl')?'-x':'y');}));
async function init(){
  try{config=await api('/api/bootstrap');const select=$('operation');const groups=new Map();for(const op of config.operations){let group=groups.get(op.family);if(!group){group=el('optgroup');group.label=op.family;select.append(group);groups.set(op.family,group);}const option=el('option',op.label);option.value=op.id;group.append(option);}
    setOperation('legoize',{orientation:'y'});setInput('datasets/others/armadillo.stl');$('connection').textContent='已连接';$('envInfo').textContent='Python · '+config.python;
    const missing=Object.entries(config.dependencies).filter(([,ready])=>!ready).map(([name])=>name);if(missing.length)showNotice('当前解释器缺少 '+missing.join('、')+'；请在 legolizer 环境启动工作台。','warning');else if(!config.strict_backend)showNotice('未找到严格后端环境，请先配置 research/opensource/legolization。','warning');
    await refresh();await loadPreview('datasets/others/armadillo.stl','y');
    const poll=async()=>{await refresh();setTimeout(poll,1500);};setTimeout(poll,1500);
  }catch(error){showNotice('工作台初始化失败：'+error.message);$('connection').textContent='连接失败';}
}
init();
